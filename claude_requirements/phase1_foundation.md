# Phase 1: Foundation

## Deliverables
- `pyproject.toml` with all dependencies
- `config.py` — settings from `.env`
- `db.py` — SQLite schema + FTS5 + sqlite-vec setup
- FastAPI app with Jinja2, base template with Pico CSS + htmx
- Topic CRUD routes with htmx forms

## Details
This phase establishes the project skeleton: installable package, database schema, and the first interactive UI (topic management). Everything else builds on this.

## Schema additions (for citation-graph scouting)
- [x] Add `semantic_scholar_id` column to `papers` table (TEXT, nullable). Semantic Scholar paper ID used for citation graph traversal.
- [x] Create `paper_citations` table: `from_paper_id` (FK), `to_paper_id` (FK), `direction` ('cites'|'cited_by'). Tracks how papers were discovered.
- [x] Add `recommendation` column to `topic_papers` table ('read'|'skip'|'deep_dive', nullable). Per-topic recommendation, not per-paper.

## Status
Complete
