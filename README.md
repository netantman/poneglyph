# Poneglyph

**An automated research-paper scouting & synthesis tool for quantitative finance research.**

Poneglyph helps you define research topics, automatically discover relevant papers by
traversing the academic citation graph, and turn each paper into structured, AI-generated
notes — then ask questions across your whole collection.

> *A poneglyph is an indestructible stone that records lost history. This one records research.*

---

## What it does

- **Topics** — Define research topics with keywords, priority keywords, and problem
  statements. Keywords drive discovery; priority keywords steer emphasis.
- **Citation scouting** — For any paper you've saved, Poneglyph walks one hop of the
  [Semantic Scholar](https://www.semanticscholar.org/product/api) citation graph
  (papers it cites + papers that cite it), filters candidates against your topic's
  keywords, and links the relevant ones. Papers outside the graph (e.g. bank research
  PDFs) fall back to LLM reference extraction.
- **Structural Skim** — A fast, cheap first-pass read of each paper (Haiku) that extracts
  the main claim, strategy type, mechanism, data, key metrics, and tables. Field labels
  are customizable per topic.
- **Deep Synthesis** — A thorough analysis (Sonnet / Opus) for papers worth a closer look.
- **Customizable "skills"** — The skim and synthesis prompts are Markdown "skill" files you
  can edit per topic, so the analysis matches how *you* read papers.
- **Cross-paper synthesis & steering** — Synthesize across all papers in a topic and get
  suggestions on where to scout next, informed by your own notes.
- **Collection Q&A** — Ask natural-language questions over your library. An intent router
  sends quick factual questions through cheap top-k retrieval, and exhaustive
  "find every paper about X" questions through a full-collection triage pass so nothing
  relevant is missed.
- **Literature management** — Browse papers, books, and blog/newsletter articles (via RSS);
  mark "read next"; attach personal notes; filter by topic or books-only.
- **Semantic + keyword search** — Local sentence-transformer embeddings power semantic
  search alongside SQLite FTS5 keyword search.

## Tech stack

- **Backend:** Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/), SQLite (FTS5)
- **Frontend:** server-rendered Jinja2 + [htmx](https://htmx.org/) + [Pico CSS](https://picocss.com/) (no build step)
- **AI:** [Anthropic Claude](https://www.anthropic.com/) (Haiku / Sonnet / Opus) for synthesis;
  [sentence-transformers](https://www.sbert.net/) (`all-MiniLM-L6-v2`) for embeddings
- **Data sources:** Semantic Scholar, arXiv, Crossref, RSS feeds

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/netantman/poneglyph.git
cd poneglyph
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e .

# 2. Configure
cp .env.example .env
#   then edit .env and set, at minimum:
#     ANTHROPIC_API_KEY=sk-ant-...
#   optional but recommended:
#     SEMANTIC_SCHOLAR_API_KEY=...   (higher citation-graph rate limits)

# 3. Run
uvicorn poneglyph.app:app --reload
#   then open http://127.0.0.1:8000
```

On first launch the SQLite database is created automatically.

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)). Key variables:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | Claude API for skim/synthesis/Q&A |
| `SEMANTIC_SCHOLAR_API_KEY` | no | Higher citation-graph rate limits (unauthenticated is heavily throttled) |
| `DATABASE_PATH` | no | SQLite location (default `data/poneglyph.db`) |
| `BACKUP_GITHUB_TOKEN` / `BACKUP_GITHUB_REPO` | no | Off-site DB backup (see [docs/RECOVERY.md](docs/RECOVERY.md)) |

> **Note:** Some paths in `poneglyph/config.py` (PDF library, ebook library) default to the
> original author's Windows/OneDrive layout. Override `pdf_base_dir` / `ebook_library_dir`
> in `.env` for your own setup.

## How it works

```
        Topic (keywords, problem statements, skills)
                          │
                          ▼
   Seed paper ──► Semantic Scholar 1-hop citations + references
                          │  (keyword-filtered; PDF-reference fallback)
                          ▼
              New papers linked to topic
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   Structural Skim (Haiku)     Deep Synthesis (Sonnet/Opus)
            │                           │
            └─────────────┬─────────────┘
                          ▼
        Cross-paper synthesis · Steering suggestions
                          │
                          ▼
        Collection Q&A  ·  Semantic + keyword search
```

## Project layout

```
poneglyph/
  app.py            FastAPI app + startup
  config.py         Settings (env-driven)
  db.py             SQLite access + schema
  pipeline.py       Scouting orchestration (discover → skim → synthesize)
  routes/           HTTP endpoints (topics, papers, scout, search, authors)
  services/         Citation graph, LLM calls, embeddings, fetchers
templates/          Jinja2 + htmx views
static/             CSS / assets
skills/             Editable Markdown skill prompts
scripts/            Backup, snapshot export/import, scheduler, maintenance
docs/               Recovery & operational docs
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a fuller module map and data flow.

## Backup & recovery

Poneglyph keeps all state in one SQLite file plus PDFs on disk. A backup script
(`scripts/backup_db.py`) snapshots the DB locally and optionally pushes a copy to a private
GitHub repo. Restore steps are in [docs/RECOVERY.md](docs/RECOVERY.md).

## Status

Personal research tool, actively developed. APIs and schema may change between commits.

## License

No license is currently specified — all rights reserved by the author. Open an issue if
you'd like to use or adapt it.
