# Phase 6: Article & Author Scouting

## Goal

Extend poneglyph beyond academic papers to scout, ingest, and synthesize blog posts and newsletter articles (Substack, personal blogs, aggregators like Quantocracy / Alpha Architect).

Three pillars:
1. A **global Authors library**, with topics opting in to a curated subset.
2. **Push-based scouting** — a daily background poller fetches new posts via RSS, gates them for relevance to each subscribed topic, and ingests matches.
3. **Unified storage** with academic papers, **tiered synthesis** that respects evidence-quality differences (peer-reviewed vs practitioner blog).

## User-facing workflow

1. User goes to the **Authors** page, adds an author or aggregator by name. LLM proposes a source URL; system verifies the URL returns a parseable RSS feed before saving. User can override / paste a URL directly.
2. On a **Topic** detail page, user opts in a subset of authors via an "Authors in scope" panel.
3. Each opt-in triggers an immediate backfill scout (last 30 days). Going forward, a daily background poller checks each subscribed (author, topic) pair for new posts.
4. Each new post is **relevance-gated** against the topic's keywords, research problems, and recent human notes. Matches are ingested as `papers` rows with `content_type='article'`. Misses are logged and skipped.
5. Article synthesis runs an **article-specific skill** that respects the same topic context. Paywalled posts are ingested as metadata + teaser, marked with a lock icon, and skipped for synthesis until the user supplies the full body.
6. Topic detail shows articles inline with academic papers, distinguished by a content-type chip and (where applicable) a lock icon. Cross-paper synthesis is unified but tiered.

## Data model

### New tables

```
authors
  id              INTEGER PRIMARY KEY
  name            TEXT NOT NULL
  byline          TEXT             -- handle / display name (distinct from `name`)
  entity_type     TEXT NOT NULL    -- 'author' | 'aggregator' | 'stub'
  source_origin   TEXT NOT NULL    -- 'manual' | 'aggregator_dereference'
  notes           TEXT
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  UNIQUE(name)

author_sources
  id              INTEGER PRIMARY KEY
  author_id       INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE
  source_type     TEXT NOT NULL    -- 'rss' | 'newsletter' | 'scrape' | 'manual'
  url             TEXT NOT NULL
  verified_at     TIMESTAMP        -- NULL until first successful fetch+parse
  last_polled_at  TIMESTAMP
  last_status     TEXT             -- 'ok' | 'http_error' | 'parse_error' | 'unverified'
  last_error      TEXT
  etag            TEXT             -- for conditional GET
  last_modified   TEXT             -- for conditional GET
  UNIQUE(author_id, url)

topic_authors
  topic_id            INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE
  author_id           INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE
  added_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  active              INTEGER NOT NULL DEFAULT 1
  scout_lookback_days INTEGER NOT NULL DEFAULT 30
  PRIMARY KEY (topic_id, author_id)

paper_fulltext       -- split out so wide bodies don't bloat the papers row
  paper_id        INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE
  body_text       TEXT
  body_html       TEXT
  source          TEXT NOT NULL    -- 'rss_full' | 'subscriber_rss' | 'manual_paste' | 'pdf_extract'
  cached_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
```

### Modifications to `papers`

```
content_type     TEXT NOT NULL DEFAULT 'academic'   -- 'academic' | 'article' | 'note'
access_status    TEXT NOT NULL DEFAULT 'public'     -- 'public' | 'paywalled' | 'subscriber_cached'
canonical_url    TEXT                                -- dedup key for articles; NULL for academic
author_id        INTEGER REFERENCES authors(id)      -- backreference; NULL for legacy academic rows
body_fetched_at  TIMESTAMP                           -- NULL until paper_fulltext row exists
```

`pdf_local_path` stays nullable. The deep-synthesis path is gated on "has body text from any source" (PDF extract or `paper_fulltext`), not on PDF presence.

### Dedup keys

| content_type | Primary dedup | Fallback |
|---|---|---|
| academic | (source, source_id) → semantic_scholar_id → title | already implemented in citation_scout._upsert_paper |
| article | canonical_url | (author_id, normalized_title) |

Add `canonical_url` to the unique constraints / indexes for article ingestion.

### Indexes

- `papers(content_type)` — list filtering
- `papers(canonical_url)` — dedup lookups
- `papers(author_id)` — "show me all posts by X"
- `author_sources(last_polled_at)` — poller's "what's due" query
- FTS5 index on `paper_fulltext.body_text` — articles need full-text search; PDFs can opt in later

## Scouting

### Add-author flow

1. UI form: name + optional hint dropdown ("Substack" / "personal site" / "RSS URL" / "let LLM guess").
2. New service `poneglyph/services/llm_suggest_author_source.py`:
   - Haiku call. Prompt includes the name, hint, and a few hard-coded patterns:
     - Substack: `{slug}.substack.com/feed`
     - Ghost / many WordPress sites: `{domain}/rss` or `{domain}/feed`
     - Aggregators: known feed URLs for Quantocracy, Alpha Architect, etc. in a small built-in dict so the LLM doesn't get to hallucinate them.
   - Returns `{candidate_url, entity_type_guess, reason}`.
3. Backend immediately fetches the candidate URL. Must satisfy:
   - HTTP 200
   - `feedparser.parse()` returns ≥1 item with non-empty title and link
   - Otherwise → return error with the proposed URL pre-filled in the form for the user to correct.
4. On success: insert `authors` + `author_sources` rows; set `verified_at = now`, `last_status = 'ok'`.
5. Manual override path: same form, user pastes URL, skip step 2, jump to step 3.

### Per-topic opt-in

- New "Authors in scope" panel on topic detail page (`templates/topics/partials/topic_authors.html`).
- Lists all global authors with a checkbox; checked = `topic_authors.active = 1`.
- On opt-in (state transition off→on): enqueue a one-shot **backfill scout** for the last `scout_lookback_days` (default 30) so the topic isn't empty until the next nightly poll.
- Backfill runs through the same ingest path as the daily poll — just with `since = now - lookback_days` instead of `since = last_polled_at`.

### Daily poll (push)

New CLI mode in `scheduler_entry.py`: `--mode article-scout`. Phase 8 (the renamed scheduler launcher) registers a daily Task Scheduler trigger for it.

For each `(topic, author, source)` triple where `topic_authors.active = 1`:

1. **Fetch RSS** with conditional GET (`If-None-Match` from stored `etag`, `If-Modified-Since` from `last_modified`). On 304, skip. On 5xx / parse error: log, set `last_status`, do not advance `last_polled_at`.
2. For each item with `published > last_polled_at`:
   - **Relevance gate** (see Skills section).
   - If author is `aggregator` → run aggregator dereference (see below) and continue with the dereferenced page.
   - **Ingest** as a `papers` row: `content_type='article'`, `canonical_url`, `author_id`, derived `access_status` (paywall heuristic below), `pdf_local_path = NULL`, `published_date` from RSS, `published_venue` from author's display name.
   - Cache body in `paper_fulltext` (`source='rss_full'`).
   - Link to topic via `topic_papers` with `is_scout_seed=0`. Note the relevance-gate score and reason in the link metadata so the user can see *why* this article landed in the topic.
   - If `access_status='public'` → run article-skill synthesis (see Skills section). If `paywalled` → skip synthesis, leave a UI banner.
3. After processing all items for the source, set `author_sources.last_polled_at = now`, persist new ETag / Last-Modified.

### Aggregator dereferencing (option c — auto-stub + review queue)

When the source author is an aggregator, an RSS item is a *pointer* to someone else's post on a different domain.

1. Fetch the linked URL (full GET, follow redirects).
2. Extract canonical author signals from the page in this order: `<meta name="author">`, `og:author`, schema.org/Person JSON-LD, `<a rel="author">`, fall back to the link's domain root (e.g. `navnoorbawa.substack.com` → name `"navnoorbawa"`, byline `"navnoorbawa.substack.com"`).
3. Look up `authors` by exact `name` match OR by an existing `author_sources.url` whose host matches the link's host. If found → use that author_id.
4. If no match → create:
   - `authors` row: `entity_type='stub'`, `source_origin='aggregator_dereference'`, name from step 2, `notes` = "Auto-created from {aggregator_name} pointer to {url}".
   - `author_sources` row: `source_type='scrape'`, `url` = post's domain root, `last_status='unverified'`.
5. Continue ingest with the new `author_id`. The article itself is real content; only the author record is provisional.
6. Stubs surface in a **"Pending authors to review"** panel on the Authors page. User can:
   - Rename / edit the stub
   - Reclassify (`stub` → `author` or `aggregator`)
   - Provide a real RSS feed URL (which gets verified and replaces the scrape source)
   - Delete the stub (cascades to leave the article with NULL `author_id`)

### Paywall heuristic

After fetching an RSS item, the body is paywalled when ANY of:
- Body length < 500 characters
- Trailing 200 chars match `r"(subscribe|continue reading|upgrade to paid|paid subscriber)"` (case-insensitive)
- Substack-specific: HTML contains `<div class="paywall"` or `id="paywall"`
- Generic: HTML contains `<meta name="article:opinion" content="paywall">` or similar publisher conventions (extend over time)

For paywalled items: store the teaser as `body_text`, set `access_status='paywalled'`, do not run the synthesis skill.

User can later supply the full body via an **"I have the full article"** button on the paper detail page. Pasting full text:
- Updates `paper_fulltext.body_text`
- Sets `access_status='subscriber_cached'`
- Triggers article-skill synthesis

### Authenticated personal RSS (deferred)

Substack lets logged-in subscribers fetch a private RSS URL that includes full bodies of paid posts. Out of scope for v1 — but the schema accommodates it: `author_sources.source_type='subscriber_rss'`, body cached with `paper_fulltext.source='subscriber_rss'`. Add a TODO note in the Authors page: "Got a Substack subscriber feed URL? Paste it as a source." Implementation in a later phase.

## Skills

### Article skill (new)

New file `skills/SKILL_SINGLE_ARTICLE_SYNTHESIS.md`, modeled on the academic single-paper skill but with article anatomy:

- **Pass 1 — Thesis**: one-sentence claim; author stance and credibility signals (practitioner, commentator, named desk experience).
- **Pass 2 — Mechanism**: causal story the author is asserting.
- **Pass 3 — Evidence**: classify as `anecdote` / `market_data_illustration` / `cited_paper` / `personal_experience` / `assertion_only`. Extract any concrete numbers (returns, Sharpe, sample sizes) — explicitly note absence as `null` rather than confabulating.
- **Pass 4 — Cross-references and actionability**: linked papers / linked posts (gold for follow-on scouting); what would a reader do differently after this; recommendation: `read` / `skip` / `save_for_reference`.

The skill is stored per topic in a new column `topics.article_skim_skill_md`, defaulting on topic creation from the canonical file in `skills/`. Same sync pattern as the existing skim skill (see `scripts/sync_skills.py`).

### Topic-context injection (in-skill conditioning)

The article-skill prompt template contains a `{topic_context}` placeholder, populated at synthesis time with:

- **Research problems** (bulleted from `topic_research_problems`)
- **Keywords** (from `topic_keywords`)
- **Recent human notes**: latest 10 non-empty `paper_notes.human_note` entries joined to papers in this topic, ordered by `updated_at DESC`, truncated to ~200 chars each

The skill is instructed: *"Read this article in the context above. Explicitly note where it agrees with, disagrees with, or extends the existing notes. If it raises a concrete question that none of the existing notes cover, surface it."*

Reuses (and extends if needed) the helper from Phase 5 / 5b that assembles the steering-suggestion context bundle. Avoid duplicating the assembly logic.

### Pre-ingest relevance gate

New service `poneglyph/services/article_relevance.py`:

```
is_relevant(topic, item) -> RelevanceResult
  RelevanceResult = { relevant: bool, score: float [0..1], reason: str }
```

- Haiku call with topic keywords + research problems + item title + item teaser. ~200 input tokens, ~80 output. Cheap.
- Cached by `(topic_id, item.guid)` to dedupe re-runs.
- Decision threshold (configurable, default `score >= 0.5`):
  - `>= 0.5` → ingest + synthesize
  - `0.3 <= score < 0.5` → ingest, mark for review, skip synthesis (surfaced in a per-topic "Review queue")
  - `< 0.3` → log decision, do not ingest
- Stored decisions visible in the steering log so the user can spot the gate misclassifying.

### Cross-paper synthesis: unified-but-tiered

Extend `llm_cross.py` (the existing cross-paper synthesis service). No schema change — tiers derive from `papers.content_type` and `papers.published_venue`:

| content_type + venue | evidence_tier |
|---|---|
| academic + recognized journal/conference | `peer_reviewed` |
| academic + arXiv/SSRN only | `working_paper` |
| article + author.entity_type='author' | `practitioner_blog` |
| article + author.entity_type='aggregator' or 'stub' | `aggregator_pointer` |

Prompt extensions:

- Each input source is tagged `[evidence_tier: ...]` in the corpus block fed to the LLM.
- Layer 1 (consensus mapping) instructions get an addition: *"Weight peer-reviewed and working papers above practitioner blogs in the consensus call. Surface practitioner-blog claims separately under a 'Practitioner perspective' subsection if they extend or contradict the academic consensus. Do not let a single high-volume blogger dominate consensus over multiple papers."*
- Output structure adds a `practitioner_perspective` field alongside the existing consensus / dissent layers.

## UI changes

- **New `/authors` page**: list, add (with LLM-suggest), edit, delete, pending-stubs panel.
- **Topic detail**: new "Authors in scope" panel (opt-in toggle per author).
- **Topic detail paper list**: content-type chip ("paper" / "article"), lock icon for `access_status='paywalled'`, author byline shown for articles.
- **Paper detail (articles)**: render `body_text` inline; "I have the full article" paste box when paywalled; relevance-gate score + reason (so user understands why it landed in this topic).
- **Topic detail "Scout Now" button**: a single unified button that triggers both citation scouting (from seed papers, if skim skill is configured) and article scouting (from subscribed authors, if any) in parallel via `POST /topics/{id}/scout-now`. Each leg that lacks prerequisites (no skim skill / no seed papers for citations; no active author subscriptions for articles) is skipped silently with a brief note. The response renders a labeled status box per leg so the user can track both independently as they complete.
- **Steering log**: log article-scout actions (added authors to topic, relevance gate decisions on borderline items) alongside existing keyword / problem-statement edits.

## Scheduler hook (handoff to Phase 8)

This phase ships `scheduler_entry.py --mode article-scout` as a callable CLI. Phase 8 (the renamed scheduler launcher) is responsible for registering the daily Windows Task Scheduler trigger that calls it, alongside the existing weekly citation scout and monthly cross-synthesis triggers.

## Dependencies

- **Phase 1** (DB foundation, models, pipeline)
- **Phase 2** (scouting pipeline pattern; reuse `_upsert_paper` shape for article ingest)
- **Phase 3** (embeddings — optional today; could power a relevance-gate v2 that uses topic_embedding cosine similarity in addition to the LLM call)
- **Phase 5** (steering log + human-note aggregation helper, reused for `{topic_context}` injection)
- **Phase 8** (scheduler — for the daily push poll trigger)

## Implementation order (suggested)

| # | Step | Status | Notes |
|---|---|---|---|
| 1 | DB migration: new `papers` columns, new `authors` / `author_sources` / `topic_authors` / `paper_fulltext` tables, indexes | ✅ Done | |
| 2 | `authors` CRUD + `/authors` UI page (manual URL entry only — no LLM yet) | ✅ Done | |
| 3 | `llm_suggest_author_source.py` + verification loop wired into add-author flow | ✅ Done | |
| 4 | RSS fetch + parse infra (`poneglyph/services/rss_fetch.py`); Test button on `/authors` page | ✅ Done | See implementation notes below |
| 5 | Topic-author opt-in panel + per-topic backfill scout on opt-in | | First push surface visible |
| 6 | `article_relevance.py` gate + integrate into ingest pipeline | | Real noise filtering |
| 7 | Article skill file + `topics.article_skim_skill_md` column + topic-context injection + sync pattern | | Synthesis works for one topic |
| 8 | Aggregator dereferencing + stub-author flow + pending-review panel | | Quantocracy / Alpha Architect work |
| 9 | Paywall heuristic + `access_status` UI (lock icon, "I have the full article" paste) | | Substack subscriber edge case |
| 10 | Cross-paper synthesis tier-aware prompt extension in `llm_cross.py` | | Unified-but-tiered output |
| 11 | `scheduler_entry.py --mode article-scout` CLI mode (Phase 8 picks up registration) | | Hand-off to scheduler phase |

## Implementation notes

### Step 4 — RSS fetch / Test button (completed 2026-05-03)

**`feedparser` must be installed in the conda env.** It is in `requirements.txt` but was missing from
the `poneglyph` conda environment, causing the test endpoint to 500 silently on every click. htmx
swallows non-2xx responses without any UI feedback, so the button appeared to do nothing.
Run `pip install feedparser` if setting up a fresh env from scratch (or `pip install -r requirements.txt`).

**Test button uses `hx-swap="outerHTML"` on the whole source-entry div**, not an htmx OOB swap.
The partial template is `templates/authors/partials/source_entry.html`. The endpoint
(`GET /authors/{author_id}/sources/{source_id}/test`) returns this partial rendered with the updated
`last_status` and inline feed preview. This replaces status icon + preview atomically and avoids
htmx 2.x OOB edge cases.

**Python 3.11 compat:** f-string expressions cannot contain backslash escapes before Python 3.12.
`authors.py` had `\'` inside an f-string generator expression, causing a `SyntaxError` at import time
and preventing the app from starting. Fixed by moving the inline HTML into a pre-computed variable
before the f-string.

## Out of scope (deliberate non-goals)

- **X / Twitter ingestion** — needs RSSHub or paid API; deferred (see scouting design discussion).
- **Authenticated Substack subscriber-feed ingestion** — manual paste path only in v1; schema accommodates future implementation.
- **Email-only newsletters** (Bloomberg's *Money Stuff*, John Authers' *Points of Return*) — would need an email-forwarding mailbox + parser; defer.
- **Embedding-based human-note retrieval per article** — use latest-N for v1; revisit when topics accumulate >100 notes.
- **Audio / video sources** (podcast transcripts).
- **Citation graph from articles** — articles cite papers via free-text links, not structured DOIs; too lossy for v1 dedup.
