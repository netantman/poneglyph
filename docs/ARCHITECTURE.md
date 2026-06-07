# Architecture

A module-level map of Poneglyph and how data flows through it. For a feature overview,
see the [README](../README.md).

## Overview

Poneglyph is a single-process FastAPI app with a server-rendered htmx frontend and a SQLite
database. There is no separate worker, queue, or build step — background work (scouting,
synthesis) runs as `asyncio` tasks within the web process, and progress is polled by htmx.

```
Browser ──htmx──► FastAPI routes ──► services ──► SQLite + external APIs
   ▲                                   │
   └──────── Jinja2 partials ◄─────────┘
```

## Layers

### `poneglyph/app.py`
FastAPI application factory. Initializes the DB on startup, recovers stale skim jobs from a
prior crash, and mounts the route modules and static files.

### `poneglyph/config.py`
`pydantic-settings` config loaded from `.env`. Holds API keys, model identifiers, DB path,
and PDF/ebook library locations.

### `poneglyph/db.py`
Thin SQLite layer: connection management, schema creation/migration, `fetch_one` /
`fetch_all` / `execute` / `transaction` helpers, and `row_to_dict` (which JSON-decodes known
columns like `keywords`, `problem_statements`, `authors`). FTS5 tables back keyword search.

### `poneglyph/pipeline.py`
Orchestration. The entry points are:
- `run_paper_enrichment(paper_id, topic_id, run_id)` — discover from one paper, then skim.
- `run_topic_scout(topic_id, run_id)` — discover from all of a topic's seed papers, then
  drain a queue of pending skims (bounded concurrency).
- `_synthesize_paper(...)` — produce the structural skim for one (paper, topic) pair.
- Article-scout helpers — pull blog/newsletter items from RSS and score relevance.

### `poneglyph/routes/`
HTTP endpoints, each returning either a full page or an htmx partial:
- `topics.py` — topic CRUD, per-topic skill editors, papers list, cross-synthesis, steering.
- `papers.py` — paper detail (per-topic view), upload, structural-skim & deep-synthesis tabs,
  per-paper Q&A, notes, read-next/unprocessed toggles.
- `scout.py` — start scout runs and poll their status.
- `search.py` — collection search (keyword + semantic) and collection Q&A (`/ask`).
- `authors.py` — author/source management.

### `poneglyph/services/`
The work happens here.

| Module | Responsibility |
|---|---|
| `semantic_scholar.py` | S2 Graph API client: paper lookup, citations, references. Rate-limited with retry/backoff; raises `S2RateLimitError` when throttled past retries. |
| `citation_scout.py` | 1-hop discovery: fetch citations/references, keyword-filter, upsert + link papers, record citation edges. PDF-reference fallback for non-graph papers. |
| `llm.py` | Anthropic client wrappers (`call_haiku`, `call_sonnet`, and PDF variants). |
| `llm_qa.py` | Collection Q&A: intent routing (factual vs enumerate), retrieval, triage, cited answers. |
| `llm_qa_paper.py` | Per-paper Q&A. |
| `llm_bulk.py`, `llm_deep.py`, `llm_cross.py` | Structural skim, deep synthesis, cross-paper synthesis. |
| `llm_metadata.py`, `llm_refs.py`, `llm_article.py` | Metadata extraction, PDF reference extraction, article synthesis. |
| `llm_suggest*.py` | Steering suggestions and author/source discovery. |
| `embeddings.py`, `relevance.py` | sentence-transformer embeddings; semantic search + relevance scoring. |
| `arxiv_fetch.py`, `crossref_fetch.py`, `rss_fetch.py`, `pdf_manager.py` | External fetchers and local PDF handling. |

### `templates/` + `static/`
Jinja2 templates with htmx attributes for interactivity. `base.html` holds shared CSS, the
toast system, a reusable modal, and the Markdown/KaTeX renderer. Partials under
`*/partials/` are returned directly by htmx endpoints.

### `skills/`
Editable Markdown prompt files ("skills") that define how papers are read and synthesized.
Per-topic overrides are stored in the DB; these files are defaults/templates.

### `scripts/`
Operational tooling: `backup_db.py` (local snapshot + optional GitHub off-site push),
`export_snapshot.py` / `import_snapshot.py`, `validate_db.py`, scheduler setup, and one-off
maintenance scripts.

## Data model (high level)

- **topics** — research topics (keywords, priority keywords, problem statements, per-topic
  skill prompts + field labels).
- **papers** — the literature (papers, books, articles) with metadata, PDF paths, S2 IDs.
- **topic_papers** — many-to-many link with per-topic flags (scout seed, not-interesting,
  relevance score).
- **topic_paper_notes** — per (topic, paper) generated content: structural skim fields, deep
  synthesis, human notes, and the skill hashes used to generate them.
- **paper_citations** — directed citation edges discovered during scouting.
- **pending_skims** — work queue for skim synthesis (with crash recovery on startup).
- **scout_runs** — status/progress of scouting runs (polled by the UI).
- **qa_history** — saved collection Q&A.
- **papers_fts** — FTS5 index for keyword search.

## Background work & status

Scouting and synthesis run as `asyncio.create_task(...)` from the scout routes. Each run
writes progress to a `scout_runs` row; the status box in the UI polls `/scout/run/{id}`
every few seconds until `finished_at` is set. On startup, skims left `pending` from a crash
are re-queued.

## External rate limits

Semantic Scholar is the main external dependency. Without an API key the shared
unauthenticated pool is small and 429s are common; `_get` in `semantic_scholar.py` retries
with exponential backoff and surfaces a typed `S2RateLimitError` so the scout reports
"rate limited — try again" instead of a misleading "0 discovered." Setting
`SEMANTIC_SCHOLAR_API_KEY` raises the limit substantially.
