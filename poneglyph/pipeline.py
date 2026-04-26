"""Scouting pipeline: citation discovery + Haiku structural skim."""

import hashlib
import json
import logging

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict, transaction
from poneglyph.services.citation_scout import _lookup_key, discover_from_paper
from poneglyph.services.llm_bulk import synthesize_paper
from poneglyph.services.semantic_scholar import get_paper as s2_get_paper

logger = logging.getLogger(__name__)


def _skill_hash(skill_md: str | None) -> str:
    """Return SHA-256 hex digest of a skill prompt, or empty string if None."""
    if not skill_md:
        return ""
    return hashlib.sha256(skill_md.encode()).hexdigest()


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
        """SELECT pn.human_note FROM paper_notes pn
           JOIN topic_papers tp ON tp.paper_id = pn.paper_id
           WHERE tp.topic_id = ? AND pn.paper_id != ?
           AND pn.human_note IS NOT NULL AND pn.human_note != ''
           ORDER BY pn.updated_at DESC
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
                   key_tables = ?, key_metrics = ?,
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
    except Exception as exc:
        logger.exception("run_paper_enrichment failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))


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
        for pid in paper_ids:
            new = await discover_from_paper(pid, topic_id)
            all_new.update(new)
            # Update running count
            execute(
                "UPDATE scout_runs SET papers_found = ? WHERE id = ?",
                (len(all_new), run_id),
            )

        synth_count = 0
        for pid in all_new:
            err = await _synthesize_paper(pid, topic)
            if not err:
                synth_count += 1
            elif err:
                logger.warning("_synthesize_paper paper=%d: %s", pid, err)

        from poneglyph.services.relevance import update_topic_relevance_scores
        update_topic_relevance_scores(topic_id)
        _finish_run(run_id, found=len(all_new), new=synth_count)
        logger.info(
            "run_topic_scout done: topic=%d seeds=%d found=%d synth=%d",
            topic_id, len(paper_ids), len(all_new), synth_count,
        )
    except Exception as exc:
        logger.exception("run_topic_scout failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))
