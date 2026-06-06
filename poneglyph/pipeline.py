"""Scouting pipeline: citation discovery + Haiku structural skim + article RSS scout."""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict, transaction
from poneglyph.services.citation_scout import (
    _lookup_key, discover_from_paper,
    extract_note_directives, search_from_directives,
)
from poneglyph.services.llm_bulk import synthesize_paper
from poneglyph.services.semantic_scholar import S2RateLimitError, get_paper as s2_get_paper

logger = logging.getLogger(__name__)

_DEREF_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:title|article:author|author)["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _skill_hash(skill_md: str | None) -> str:
    """Return SHA-256 hex digest of a skill prompt, or empty string if None."""
    if not skill_md:
        return ""
    return hashlib.sha256(skill_md.encode()).hexdigest()


def _ensure_topic_paper_note(topic_id: int, paper_id: int) -> None:
    """INSERT OR IGNORE a topic_paper_notes row so subsequent UPDATEs have a target."""
    execute(
        "INSERT OR IGNORE INTO topic_paper_notes (topic_id, paper_id) VALUES (?, ?)",
        (topic_id, paper_id),
    )


# ---------- Run lifecycle ----------

def create_run(topic_id: int | None, source: str) -> int:
    return execute(
        "INSERT INTO scout_runs (topic_id, source) VALUES (?, ?)",
        (topic_id, source),
    )


def _finish_run(run_id: int, *, found: int, new: int, status: str = "ok", error: str = "") -> None:
    execute(
        """UPDATE scout_runs
           SET papers_found = ?, papers_new = ?, status = ?, error_message = ?,
               finished_at = datetime('now')
           WHERE id = ?""",
        (found, new, status, error or None, run_id),
    )


# ---------- Synthesis helper ----------

async def _synthesize_paper(paper_id: int, topic: dict) -> str:
    """Run Haiku structural skim for one paper in one topic and persist results.

    Writes to topic_paper_notes (per-(paper,topic)) and mirrors skim_recommendation
    to topic_papers.recommendation for the list view.
    Returns "" on success, or a human-readable error string on failure.
    """
    paper = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))
    if not paper:
        return "Paper not found in database"

    topic_id = topic["id"]

    # Gather human notes from other papers in the topic for context
    note_rows = fetch_all(
        """SELECT tpn.human_note FROM topic_paper_notes tpn
           WHERE tpn.topic_id = ? AND tpn.paper_id != ?
             AND tpn.human_note IS NOT NULL AND tpn.human_note != ''
           ORDER BY tpn.id DESC
           LIMIT 5""",
        (topic_id, paper_id),
    )
    related_notes = [r["human_note"] for r in note_rows]

    result, err = await synthesize_paper(paper, topic, related_notes)
    if err:
        return err
    if not result:
        return ""  # no skill set — silent skip, not an error

    skill_hash = _skill_hash(topic.get("skim_skill_md") or "")
    recommendation = result.get("skim_recommendation", "skip")

    # Atomic: ensure row exists, write skim fields, mirror recommendation. If any
    # statement fails, the whole skim write rolls back instead of leaving a
    # half-written topic_paper_notes row paired with stale topic_papers data.
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO topic_paper_notes (topic_id, paper_id) VALUES (?, ?)",
            (topic_id, paper_id),
        )
        conn.execute(
            """UPDATE topic_paper_notes
               SET main_claim = ?, data_source = ?, strategy_type = ?,
                   headline_statistic = ?, signal_mechanism = ?, data_details = ?,
                   sample = ?, universe = ?, portfolio_construction = ?,
                   key_tables = ?, key_metrics = ?, skip_reason = ?,
                   skim_recommendation = ?, skim_model_used = 'claude-haiku-4-5-20251001',
                   skim_skill_hash = ?, skim_generated_at = datetime('now'),
                   skim_pdf_used = ?
               WHERE topic_id = ? AND paper_id = ?""",
            (
                result.get("main_claim", ""),
                result.get("data_source", ""),
                result.get("strategy_type", ""),
                result.get("headline_statistic", ""),
                result.get("signal_mechanism", ""),
                result.get("data_details", ""),
                result.get("sample", ""),
                result.get("universe", ""),
                result.get("portfolio_construction", ""),
                json.dumps(result.get("key_tables", [])),
                result.get("key_metrics", ""),
                result.get("skip_reason", ""),
                recommendation,
                skill_hash,
                1 if result.get("pdf_used") else 0,
                topic_id,
                paper_id,
            ),
        )
        conn.execute(
            "UPDATE topic_papers SET recommendation = ? WHERE topic_id = ? AND paper_id = ?",
            (recommendation, topic_id, paper_id),
        )
    return ""


# ---------- S2 ID back-fill ----------

async def resolve_missing_s2_ids(max_papers: int = 200) -> int:
    """Resolve Semantic Scholar IDs for all papers that don't have one yet.

    Iterates papers where semantic_scholar_id is NULL or empty, attempts to
    resolve via S2 using the best available identifier (arXiv ID, DOI, URL),
    and back-fills the column. Respects the S2 rate limiter (1 req/s).

    Returns the number of papers successfully resolved.
    """
    rows = fetch_all(
        """SELECT * FROM papers
           WHERE semantic_scholar_id IS NULL OR semantic_scholar_id = ''
           ORDER BY created_at DESC
           LIMIT ?""",
        (max_papers,),
    )
    if not rows:
        return 0

    logger.info("resolve_missing_s2_ids: %d papers to resolve", len(rows))
    resolved = 0
    for row in rows:
        paper = row_to_dict(row)
        lookup = _lookup_key(paper)
        if not lookup:
            continue
        data = await s2_get_paper(lookup)
        if not data:
            continue
        s2_id = data.get("paperId") or ""
        if not s2_id:
            continue
        execute(
            "UPDATE papers SET semantic_scholar_id = ? WHERE id = ? "
            "AND (semantic_scholar_id IS NULL OR semantic_scholar_id = '')",
            (s2_id, paper["id"]),
        )
        resolved += 1

    logger.info("resolve_missing_s2_ids: resolved %d / %d", resolved, len(rows))
    return resolved


# ---------- Public pipeline entry points ----------

async def run_paper_enrichment(paper_id: int, topic_id: int, run_id: int) -> None:
    """Discover citations/references for one paper, synthesize new ones.

    Updates scout_runs row when done. Never raises.
    """
    try:
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        new_ids = await discover_from_paper(paper_id, topic_id)
        execute("UPDATE scout_runs SET papers_found = ? WHERE id = ?", (len(new_ids), run_id))

        synth_count = 0
        for pid in new_ids:
            err = await _synthesize_paper(pid, topic)
            if not err:
                synth_count += 1
            elif err:
                logger.warning("_synthesize_paper paper=%d: %s", pid, err)

        from poneglyph.services.relevance import update_topic_relevance_scores
        update_topic_relevance_scores(topic_id)
        _finish_run(run_id, found=len(new_ids), new=synth_count)
        logger.info(
            "run_paper_enrichment done: paper=%d topic=%d found=%d synth=%d",
            paper_id, topic_id, len(new_ids), synth_count,
        )
    except S2RateLimitError:
        logger.warning("run_paper_enrichment paper=%d: S2 rate limited", paper_id)
        _finish_run(
            run_id, found=0, new=0, status="error",
            error="Semantic Scholar rate limited — wait a minute and try again.",
        )
    except Exception as exc:
        logger.exception("run_paper_enrichment failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))


def queue_skim_for_topic(topic_id: int, paper_id: int) -> None:
    """Enqueue a (topic, paper) pair for skim synthesis. Idempotent."""
    execute(
        "INSERT OR IGNORE INTO pending_skims (topic_id, paper_id, status) VALUES (?, ?, 'pending')",
        (topic_id, paper_id),
    )


async def _drain_pending_skims(topic_id: int, topic: dict) -> tuple[int, int]:
    """Process all pending_skims rows for this topic with up to 3 concurrent skim calls.

    Returns (synth_count, error_count).
    """
    import asyncio

    semaphore = asyncio.Semaphore(3)

    rows = fetch_all(
        "SELECT paper_id FROM pending_skims WHERE topic_id = ? AND status = 'pending'",
        (topic_id,),
    )

    synth_count = 0
    error_count = 0

    async def _process(paper_id: int) -> None:
        nonlocal synth_count, error_count
        async with semaphore:
            err = await _synthesize_paper(paper_id, topic)
            if err:
                execute(
                    "UPDATE pending_skims SET status='error', error_msg=? WHERE topic_id=? AND paper_id=?",
                    (err[:500], topic_id, paper_id),
                )
                error_count += 1
                logger.warning("_drain_pending_skims paper=%d: %s", paper_id, err)
            else:
                execute(
                    "UPDATE pending_skims SET status='done' WHERE topic_id=? AND paper_id=?",
                    (topic_id, paper_id),
                )
                synth_count += 1

    await asyncio.gather(*[_process(r["paper_id"]) for r in rows])
    return synth_count, error_count


async def run_topic_scout(topic_id: int, run_id: int) -> None:
    """Discover citations for seed papers in a topic, then synthesize new ones.

    Only papers with is_scout_seed=1 are used as traversal starting points.
    Updates scout_runs row when done. Never raises.
    """
    try:
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        # Back-fill S2 IDs for any existing papers that are missing them before scouting
        await resolve_missing_s2_ids()

        paper_rows = fetch_all(
            "SELECT paper_id FROM topic_papers WHERE topic_id = ? AND is_scout_seed = 1",
            (topic_id,),
        )
        paper_ids = [r["paper_id"] for r in paper_rows]

        if not paper_ids:
            logger.warning("run_topic_scout: topic=%d has no seed papers — skipping", topic_id)
            _finish_run(run_id, found=0, new=0, status="no_seeds")
            return

        all_new: set[int] = set()
        rate_limited = False
        for pid in paper_ids:
            try:
                new = await discover_from_paper(pid, topic_id)
            except S2RateLimitError:
                logger.warning("run_topic_scout: topic=%d rate limited mid-traversal", topic_id)
                rate_limited = True
                break
            all_new.update(new)
            # Update running count
            execute(
                "UPDATE scout_runs SET papers_found = ? WHERE id = ?",
                (len(all_new), run_id),
            )

        # Note-driven directives: scan human notes for explicit scouting instructions
        directives = [] if rate_limited else extract_note_directives(topic_id)
        if directives:
            logger.info(
                "run_topic_scout: topic=%d found %d scouting directive(s) in notes",
                topic_id, len(directives),
            )
            directive_new = await search_from_directives(directives, topic_id)
            all_new.update(directive_new)
            execute(
                "UPDATE scout_runs SET papers_found = ? WHERE id = ?",
                (len(all_new), run_id),
            )

        # Queue all new papers for skim synthesis via pending_skims
        for pid in all_new:
            execute(
                "INSERT OR IGNORE INTO pending_skims (topic_id, paper_id, status) VALUES (?, ?, 'pending')",
                (topic_id, pid),
            )

        synth_count, err_count = await _drain_pending_skims(topic_id, topic)

        from poneglyph.services.relevance import update_topic_relevance_scores
        update_topic_relevance_scores(topic_id)
        if rate_limited:
            _finish_run(
                run_id, found=len(all_new), new=synth_count, status="error",
                error="Semantic Scholar rate limited mid-scout — partial results saved. "
                      "Wait a minute and run again to continue.",
            )
        else:
            _finish_run(run_id, found=len(all_new), new=synth_count)
        logger.info(
            "run_topic_scout done: topic=%d seeds=%d found=%d synth=%d errors=%d rate_limited=%s",
            topic_id, len(paper_ids), len(all_new), synth_count, err_count, rate_limited,
        )
    except S2RateLimitError:
        logger.warning("run_topic_scout topic=%d: S2 rate limited", topic_id)
        _finish_run(
            run_id, found=0, new=0, status="error",
            error="Semantic Scholar rate limited — wait a minute and try again.",
        )
    except Exception as exc:
        logger.exception("run_topic_scout failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))


# ── Article scout pipeline ────────────────────────────────────────────────────

def _canonical_source_id(canonical_url: str) -> str:
    """Derive a short stable source_id from a canonical URL."""
    import hashlib as _hl
    return _hl.sha256(canonical_url.encode()).hexdigest()[:24]


async def _fetch_article_meta(url: str) -> dict:
    """Fetch an external URL and extract og:/meta title, author, published_time.

    Returns a dict with keys: title, author, published_date (YYYY-MM-DD or ""), description.
    Best-effort — never raises.
    """
    meta: dict = {"title": "", "author": "", "published_date": "", "description": ""}
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Poneglyph/1.0 research-scout"},
            )
        if resp.status_code != 200:
            return meta
        html = resp.text[:50_000]
    except Exception as exc:
        logger.debug("_fetch_article_meta: %s — %s", url, exc)
        return meta

    def _og(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\'](?:og:{prop}|{prop}|article:{prop})["\'][^>]*'
            rf'content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        return m.group(1).strip() if m else ""

    meta["title"] = _og("title") or ""
    meta["author"] = _og("author") or ""
    meta["description"] = _og("description") or ""
    pub = _og("published_time") or _og("published") or ""
    if pub:
        meta["published_date"] = pub[:10]
    return meta


def _find_or_create_stub_author(domain: str, author_name: str, notes: str = "") -> int:
    """Return the author_id for a domain, creating a stub if necessary."""
    # Match by existing author_sources URL host
    sources = fetch_all("SELECT author_id, url FROM author_sources", ())
    for row in sources:
        try:
            if urlparse(row["url"]).netloc == domain:
                return row["author_id"]
        except Exception:
            pass

    name = author_name.strip() or domain
    # Try exact name match first
    existing = fetch_one("SELECT id FROM authors WHERE name = ?", (name,))
    if existing:
        return existing["id"]

    author_id = execute(
        """INSERT OR IGNORE INTO authors (name, entity_type, source_origin, notes)
           VALUES (?, 'stub', 'aggregator_dereference', ?)""",
        (name, notes or f"Auto-created from aggregator pointer to {domain}"),
    )
    if not author_id:
        row = fetch_one("SELECT id FROM authors WHERE name = ?", (name,))
        author_id = row["id"] if row else None

    if author_id:
        execute(
            "INSERT OR IGNORE INTO author_sources (author_id, source_type, url, last_status) VALUES (?, 'scrape', ?, 'unverified')",
            (author_id, f"https://{domain}"),
        )

    return author_id or 0


async def _ingest_rss_item(
    item,  # RssItem
    topic: dict,
    author_id: int,
    is_aggregator: bool,
) -> tuple[int | None, bool, str]:
    """Ingest one RSS item (or its dereferenced target) as a papers row.

    For aggregators, fetches the linked URL and uses its metadata instead.
    Returns (paper_id, is_new, access_status).
    """
    from poneglyph.services.rss_fetch import is_paywalled, item_body

    canonical_url = item.link
    title = item.title
    author_name = item.author_name
    pub_dt = item.published_dt
    body_html = item_body(item)
    body_text = ""
    paywalled = is_paywalled(item)

    resolved_author_id = author_id

    if is_aggregator and canonical_url:
        # Dereference: fetch the real article page
        meta = await _fetch_article_meta(canonical_url)
        if meta.get("title"):
            title = meta["title"]
        if meta.get("published_date"):
            try:
                pub_dt = datetime.fromisoformat(meta["published_date"]).replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if meta.get("author"):
            author_name = meta["author"]

        domain = urlparse(canonical_url).netloc or canonical_url
        stub_notes = f"Auto-created from aggregator pointer to {canonical_url}"
        resolved_author_id = _find_or_create_stub_author(domain, author_name, stub_notes)

    # Dedup by canonical_url
    existing = fetch_one("SELECT id FROM papers WHERE canonical_url = ?", (canonical_url,)) if canonical_url else None
    if existing:
        return existing["id"], False, "public"

    # Build authors list
    authors_json = json.dumps([author_name] if author_name else [])
    pub_date_str = pub_dt.strftime("%Y-%m-%d") if pub_dt else ""

    # Get author's display name for published_venue
    author_row = row_to_dict(fetch_one("SELECT name FROM authors WHERE id = ?", (resolved_author_id,)))
    venue = author_row["name"] if author_row else ""

    access_status = "paywalled" if paywalled else "public"
    source_id = _canonical_source_id(canonical_url) if canonical_url else ""

    paper_id = execute(
        """INSERT OR IGNORE INTO papers
           (source, source_id, title, authors, published_venue, published_date,
            url, abstract, content_type, access_status, canonical_url, author_id)
           VALUES ('rss', ?, ?, ?, ?, ?, ?, ?, 'article', ?, ?, ?)""",
        (
            source_id, title, authors_json, venue, pub_date_str,
            canonical_url or "", (item.summary or "")[:2000],
            access_status, canonical_url, resolved_author_id,
        ),
    )

    if not paper_id:
        # IGNORE fired — already exists under (source, source_id)
        existing2 = fetch_one("SELECT id FROM papers WHERE source='rss' AND source_id=?", (source_id,))
        return (existing2["id"] if existing2 else None), False, access_status

    # Cache body in paper_fulltext
    if body_html or item.summary:
        from poneglyph.services.llm_bulk import strip_html
        body_text = strip_html(body_html) if body_html else item.summary or ""
        execute(
            """INSERT OR REPLACE INTO paper_fulltext (paper_id, body_text, body_html, source)
               VALUES (?, ?, ?, 'rss_full')""",
            (paper_id, body_text[:80_000], (body_html or "")[:200_000]),
        )
        execute(
            "UPDATE papers SET body_fetched_at = datetime('now') WHERE id = ?",
            (paper_id,),
        )

    return paper_id, True, access_status


async def _synthesize_article_paper(paper_id: int, topic: dict, body_text: str) -> str:
    """Run article skill synthesis for one paper in one topic. Returns "" on success."""
    from poneglyph.services.llm_article import synthesize_article, skill_hash

    paper = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))
    if not paper:
        return "Paper not found"

    topic_id = topic["id"]

    note_rows = fetch_all(
        """SELECT tpn.human_note FROM topic_paper_notes tpn
           WHERE tpn.topic_id = ? AND tpn.paper_id != ?
             AND tpn.human_note IS NOT NULL AND tpn.human_note != ''
           ORDER BY tpn.id DESC LIMIT 10""",
        (topic_id, paper_id),
    )
    related_notes = [r["human_note"] for r in note_rows]

    result, err = await synthesize_article(paper, topic, body_text, related_notes)
    if err:
        return err
    if not result:
        return ""

    sh = skill_hash(topic.get("article_skim_skill_md") or "")
    recommendation = result.get("skim_recommendation", "skip")

    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO topic_paper_notes (topic_id, paper_id) VALUES (?, ?)",
            (topic_id, paper_id),
        )
        conn.execute(
            """UPDATE topic_paper_notes
               SET main_claim=?, data_source=?, strategy_type=?,
                   headline_statistic=?, signal_mechanism=?, data_details=?,
                   sample=?, universe=?, portfolio_construction=?,
                   key_tables=?, key_metrics=?, skip_reason=?,
                   skim_recommendation=?, skim_model_used=?,
                   skim_skill_hash=?, skim_generated_at=datetime('now'), skim_pdf_used=0
               WHERE topic_id=? AND paper_id=?""",
            (
                result.get("main_claim", ""),
                result.get("data_source", ""),
                result.get("strategy_type", ""),
                result.get("headline_statistic", ""),
                result.get("signal_mechanism", ""),
                result.get("data_details", ""),
                result.get("sample", ""),
                result.get("universe", ""),
                result.get("portfolio_construction", ""),
                json.dumps(result.get("key_tables", [])),
                result.get("key_metrics", ""),
                result.get("skip_reason", ""),
                recommendation,
                "claude-haiku-4-5-20251001",
                sh,
                topic_id, paper_id,
            ),
        )
        conn.execute(
            "UPDATE topic_papers SET recommendation=? WHERE topic_id=? AND paper_id=?",
            (recommendation, topic_id, paper_id),
        )
    return ""


async def run_article_scout_for_topic(
    topic_id: int,
    run_id: int,
    since: datetime | None = None,
) -> None:
    """Scout RSS feeds for all subscribed authors in a topic.

    since: if None, uses each source's last_polled_at. Pass an explicit datetime for backfill.
    Never raises — all errors are logged and recorded in scout_runs.
    """
    try:
        from poneglyph.services.rss_fetch import fetch_feed
        from poneglyph.services.article_relevance import is_relevant

        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        # Load the article skill from canonical file if topic has none
        if not topic.get("article_skim_skill_md"):
            _load_default_article_skill(topic_id)
            topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))

        subscriptions = fetch_all(
            """SELECT ta.author_id, ta.scout_lookback_days, a.entity_type, a.name AS author_name
               FROM topic_authors ta
               JOIN authors a ON a.id = ta.author_id
               WHERE ta.topic_id = ? AND ta.active = 1""",
            (topic_id,),
        )
        if not subscriptions:
            _finish_run(run_id, found=0, new=0, status="no_authors")
            return

        total_found = 0
        total_new = 0

        for sub in subscriptions:
            author_id = sub["author_id"]
            is_aggregator = sub["entity_type"] == "aggregator"
            lookback_days = sub["scout_lookback_days"] or 30

            sources = fetch_all(
                "SELECT * FROM author_sources WHERE author_id = ? AND last_status != 'http_error'",
                (author_id,),
            )

            for src in sources:
                src_dict = row_to_dict(src)
                source_id_db = src_dict["id"]

                # Determine cutoff
                if since is not None:
                    cutoff = since
                elif src_dict.get("last_polled_at"):
                    try:
                        cutoff = datetime.fromisoformat(src_dict["last_polled_at"]).replace(tzinfo=timezone.utc)
                    except Exception:
                        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                else:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

                result = await fetch_feed(
                    src_dict["url"],
                    etag=src_dict.get("etag"),
                    last_modified=src_dict.get("last_modified"),
                )

                if result.error:
                    execute(
                        "UPDATE author_sources SET last_status=?, last_error=? WHERE id=?",
                        ("http_error", result.error[:500], source_id_db),
                    )
                    continue

                # Update ETag / Last-Modified
                execute(
                    "UPDATE author_sources SET etag=?, last_modified=?, last_status='ok', last_error=NULL WHERE id=?",
                    (result.etag, result.last_modified, source_id_db),
                )

                if result.not_modified:
                    continue

                new_items = [
                    item for item in result.items
                    if item.published_dt is None or item.published_dt >= cutoff
                ]

                for item in new_items:
                    total_found += 1

                    # Relevance gate
                    rel = await is_relevant(topic, item.title, item.summary or "")
                    log_reason = f"score={rel.score:.2f} {rel.reason}"

                    if not rel.relevant and not rel.borderline:
                        logger.debug(
                            "article_scout: skip '%s' (%s)", item.title[:60], log_reason
                        )
                        continue

                    paper_id, is_new, access_status = await _ingest_rss_item(
                        item, topic, author_id, is_aggregator
                    )
                    if not paper_id:
                        continue

                    # Link to topic
                    execute(
                        """INSERT OR IGNORE INTO topic_papers
                           (topic_id, paper_id, matched_keywords, relevance_score, is_scout_seed)
                           VALUES (?, ?, '[]', ?, 0)""",
                        (topic_id, paper_id, rel.score),
                    )
                    # Log relevance decision in steering log
                    execute(
                        "INSERT INTO topic_steering_log (topic_id, change_description) VALUES (?, ?)",
                        (
                            topic_id,
                            f"Article scout: {'ingested' if is_new else 'linked'} "
                            f"'{item.title[:80]}' ({log_reason})"
                            + (" [borderline]" if rel.borderline else ""),
                        ),
                    )

                    if is_new:
                        total_new += 1

                    # Synthesize public articles
                    if access_status == "public" and not rel.borderline:
                        ft_row = row_to_dict(
                            fetch_one("SELECT body_text FROM paper_fulltext WHERE paper_id=?", (paper_id,))
                        )
                        body_text = (ft_row or {}).get("body_text") or ""
                        if body_text:
                            err = await _synthesize_article_paper(paper_id, topic, body_text)
                            if err:
                                logger.warning("_synthesize_article_paper paper=%d: %s", paper_id, err)

                execute(
                    "UPDATE author_sources SET last_polled_at=datetime('now') WHERE id=?",
                    (source_id_db,),
                )

            execute(
                "UPDATE scout_runs SET papers_found=?, papers_new=? WHERE id=?",
                (total_found, total_new, run_id),
            )

        _finish_run(run_id, found=total_found, new=total_new)
        logger.info(
            "run_article_scout_for_topic done: topic=%d found=%d new=%d",
            topic_id, total_found, total_new,
        )
    except Exception as exc:
        logger.exception("run_article_scout_for_topic failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))


def _load_default_article_skill(topic_id: int) -> None:
    """Populate topics.article_skim_skill_md from the canonical skill file if it's NULL."""
    from pathlib import Path

    skill_path = (
        Path(__file__).resolve().parent.parent
        / "skills"
        / "SKILL_SINGLE_ARTICLE_SYNTHESIS.md"
    )
    if not skill_path.exists():
        return
    skill_md = skill_path.read_text(encoding="utf-8")
    execute(
        "UPDATE topics SET article_skim_skill_md=? WHERE id=? AND article_skim_skill_md IS NULL",
        (skill_md, topic_id),
    )


async def run_all_article_scouts(run_id: int | None = None) -> None:
    """Run article scout for every topic that has active author subscriptions.

    Intended for the scheduler's --mode article-scout invocation.
    """
    topic_rows = fetch_all(
        """SELECT DISTINCT ta.topic_id FROM topic_authors ta
           WHERE ta.active = 1""",
        (),
    )
    if not topic_rows:
        logger.info("run_all_article_scouts: no active subscriptions")
        return

    for row in topic_rows:
        tid = row["topic_id"]
        rid = run_id or create_run(tid, "article_scout")
        await run_article_scout_for_topic(tid, rid)
