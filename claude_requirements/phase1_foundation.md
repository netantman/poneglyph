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

## Schema additions (user workflow)
- [x] Add `read_next` boolean column to `papers` table (INTEGER, default 0). User-set flag to mark papers for next reading. Togglable from both paper list and paper detail pages.

## Schema additions (per-topic skills + per-(paper, topic) synthesis)
- [ ] Add `skim_skill_md` TEXT column to `topics` (nullable, default NULL). Markdown prompt for the topic's Structural Skim skill (Phase 2 / Haiku).
- [ ] Add `deep_synthesis_skill_md` TEXT column to `topics` (nullable, default NULL). Markdown prompt for the topic's Deep Synthesis skill (Phase 4 / Sonnet or Opus).
- [ ] Create `topic_paper_notes` table: UNIQUE(topic_id, paper_id). Stores structural skim + deep synthesis per (paper, topic) pair:
  - Structural skim (Phase 2, Haiku): `main_claim`, `data_source`, `strategy_type`, `headline_statistic`, `signal_mechanism`, `data_details`, `sample`, `universe`, `portfolio_construction`, `key_tables` (JSON), `key_metrics`, `skim_recommendation` CHECK IN ('read','skip','deep_dive'), `skim_model_used`, `skim_skill_hash` (SHA-256 of the topic's skim_skill_md at generation time), `skim_generated_at`
  - Deep synthesis (Phase 4): `deep_synthesis` TEXT, `deep_synthesis_model_used`, `deep_skill_hash`, `deep_generated_at`
  - Foreign keys on `(topic_id, paper_id)` → `topic_papers(topic_id, paper_id)` with ON DELETE CASCADE so unlinking a topic removes its per-pair outputs
- [ ] Migration: drop the structural-skim columns from `paper_notes` (keep only `human_note`, `paper_info`, `abstract_excerpt`, timestamps). If existing data is present, migrate rows where a paper has exactly one topic linkage into the corresponding `topic_paper_notes` row; drop others.
- [ ] `paper_notes` remains paper-level (shared across topics).

## Status
Complete for initial schema. New skim/deep/skills additions are **pending** — part of the per-topic synthesis refactor.
