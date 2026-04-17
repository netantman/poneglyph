# Poneglyph - Research Paper Scouting Webapp

## Context
Build a webapp that scouts research papers via **citation graph traversal** (Semantic Scholar API). Users establish topics by uploading seed papers, defining problem statements, and **uploading two per-topic skill prompts**: a **Structural Skim skill** (Haiku, Phase 2) and a **Deep Synthesis skill** (Sonnet/Opus, Phase 4). The system discovers new papers by following citations and references from seed papers, then filters by relevance. Papers are stored locally in SQLite. Because a paper can belong to multiple topics, its structural skim and deep synthesis are produced and stored **per (paper, topic) pair** — one skim/synthesis per topic lens. Cross-paper synthesis provides research direction guidance. Runs weekly via Windows Task Scheduler.

## Tech Stack
- **Backend**: Python FastAPI + Jinja2 templates + htmx (no build step, no node_modules)
- **Database**: SQLite (single file, free) with FTS5 (keyword search) + sqlite-vec (vector search)
- **Citation API**: Semantic Scholar Academic Graph API (free, 100 req/sec unauthenticated)
- **Metadata API**: arXiv Atom API (for arXiv-specific metadata enrichment)
- **Embeddings**: sentence-transformers `all-MiniLM-L6-v2` (22MB, runs locally, free)
- **LLM**: Claude Haiku for structural skim (Phase 2, ~$0.001/paper), Claude Sonnet for deep synthesis and cross-paper synthesis (Phase 4, user-approved). Both passes use per-topic skill prompts uploaded by the user. **Always use the latest Haiku and Sonnet models** — configured in `config.py` as `haiku_model` and `sonnet_model`; update those values when Anthropic releases newer versions. Current: `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`.
- **CSS**: Pico CSS via CDN (clean defaults, zero config)
- **Scheduler**: Windows Task Scheduler calling a Python script

## Project Structure
```
poneglyph/
├── pyproject.toml
├── .env.example
├── poneglyph/
│   ├── app.py              # FastAPI app factory
│   ├── config.py            # Settings from .env
│   ├── db.py                # SQLite + FTS5 + sqlite-vec setup
│   ├── models.py            # Pydantic schemas
│   ├── pipeline.py          # Orchestration: discover -> store -> synthesize
│   ├── scheduler_entry.py   # CLI entry for Windows Task Scheduler
│   ├── routes/
│   │   ├── topics.py        # Topic CRUD + synthesis triggers (per-topic)
│   │   ├── papers.py        # Paper listing, detail, upload, human notes
│   │   ├── scouting.py      # Manual scouting & citation enrichment triggers
│   │   └── settings.py      # App settings
│   └── services/
│       ├── semantic_scholar.py  # Semantic Scholar API: citations, references, metadata
│       ├── arxiv_fetch.py       # arXiv API: metadata enrichment for arXiv papers
│       ├── citation_scout.py    # Citation graph traversal logic (1-hop, both directions)
│       ├── pdf_manager.py       # PDF download/storage
│       ├── embeddings.py        # Embedding generation + vector search + relevance scoring
│       ├── llm_bulk.py          # Haiku: structural skim (Pass 1 + Pass 2), per-topic skill
│       ├── llm_deep.py          # Sonnet/Opus: deep synthesis (user-approved), per-topic skill
│       ├── llm_cross.py         # Cross-paper synthesis (user-triggered from topic page)
│       └── llm_qa.py            # Q&A over paper collection
├── templates/               # Jinja2 + htmx
├── static/                  # Pico CSS, htmx.min.js
├── data/                    # Runtime: poneglyph.db, pdfs/
└── scripts/
    └── setup_scheduler.py   # Register Windows Task Scheduler
```

## Database Schema

### Core tables
- **topics**: name, description, keywords (JSON), problem_statements (JSON), pdf_policy ('link_only'|'download'), is_active, **skim_skill_md** (TEXT), **deep_synthesis_skill_md** (TEXT)
  - `keywords`: used for keyword-based search filtering and FTS5 queries
  - `problem_statements`: specific problems the user wants solutions to — drives relevance scoring and LLM prompts
  - `skim_skill_md`: the prompt template (Markdown) uploaded by the user for the Phase 2 Haiku structural skim. **Required**; no built-in default. If NULL, synthesis is silently skipped.
  - `deep_synthesis_skill_md`: the prompt template (Markdown) uploaded by the user for the Phase 4 Sonnet/Opus deep synthesis. **Required**; no built-in default.
- **papers**: source, source_id, semantic_scholar_id, title, authors, published_venue, published_date, abstract, url, pdf_url, pdf_local_path, read_next
  - `semantic_scholar_id`: Semantic Scholar paper ID (e.g. "649def34f8be52c8b66281af98ae884c09aef38b"). Used for citation graph traversal.
  - `read_next`: boolean (INTEGER 0/1, default 0). User-set flag to mark papers for next reading. Togglable from paper list and paper detail pages.
- **topic_papers**: many-to-many linking + relevance_score (semantic similarity to topic's problem statements) + recommendation ('read'|'skip'|'deep_dive') + `is_scout_seed` (INTEGER 0/1, default 0) — recommendation is per-topic, not per-paper; `is_scout_seed` controls which papers are used as citation traversal starting points during scouting
- **paper_citations**: from_paper_id, to_paper_id, direction ('cites'|'cited_by')
  - Tracks the citation graph. `from_paper_id` cites `to_paper_id` when direction='cites'. Used to trace how papers were discovered.

### Note tables
- **paper_notes**: paper-level fields that are shared across topics (one row per paper):
  - `human_note`: user-entered free text (nullable) — annotation and steering input (shared across topics)
  - `abstract_excerpt`, `paper_info` (JSON)
- **topic_paper_notes**: structural skim + deep synthesis per (paper, topic) pair. UNIQUE(topic_id, paper_id). Columns:
  - Structural skim (Phase 2 / Haiku): `main_claim`, `data_source`, `strategy_type`, `headline_statistic`, `signal_mechanism`, `data_details`, `sample`, `universe`, `portfolio_construction`, `key_tables` (JSON), `key_metrics`, `skim_recommendation` (read|skip|deep_dive), `skim_model_used`, `skim_skill_hash` (hash of the topic's skim_skill_md at generation time — used to flag staleness when the skill changes), `skim_generated_at`
  - Deep synthesis (Phase 4 / Sonnet or Opus): `deep_synthesis` (rich text / markdown), `deep_synthesis_model_used`, `deep_skill_hash`, `deep_generated_at`
  - Cascade-deleted when either the topic or the paper is deleted, or when the topic_paper association is removed
- **cross_syntheses**: topic_id, paper_ids (JSON), synthesis, research_directions (JSON), model_used, created_at

### Operational tables
- **scout_runs**: logging table for each scouting run
- **topic_steering_log**: tracks changes to topic emphasis/direction over time

### Search tables
- **papers_fts**: FTS5 virtual table (keyword search across title, abstract, paper_notes fields)
- **paper_embeddings**: sqlite-vec virtual table (384-dim vectors)
- **topic_embeddings**: sqlite-vec virtual table (384-dim vectors) — one embedding per problem statement per topic

## Core Workflow: Citation-Graph Scouting

### 1. Topic Establishment
1. User creates a topic with a name, description, and problem statements
2. User **uploads/edits the topic's two skill prompts** (Structural Skim skill for Haiku, Deep Synthesis skill for Sonnet/Opus). Both are required before scouting or any single-paper LLM pass can occur for this topic — "Scout Now" and "Generate Structural Skim" stay disabled until the Structural Skim skill is uploaded; "Generate Deep Synthesis" stays disabled until the Deep Synthesis skill is uploaded.
3. User uploads initial papers (via arXiv URL, other URL, or manual entry)
4. System resolves each paper's Semantic Scholar ID
5. System fetches 1-hop citations (papers that cite it) and references (papers it cites) from Semantic Scholar
6. New papers are stored; Haiku structural skim runs on each using **this topic's Structural Skim skill**, producing one `topic_paper_notes` row per (paper, topic) pair.

### 2. Ongoing Scouting (scheduled or manual)
1. For each active topic, get papers with `is_scout_seed = 1` in `topic_papers`
   (topics with no seeds are skipped with a logged warning)
2. Query Semantic Scholar for new citations of seed papers (papers not already in DB)
3. Filter by keyword relevance: paper title/abstract must match at least one topic keyword or
   significant term from problem statements
4. Score relevance against problem statements via embeddings
5. Store new papers, run Haiku synthesis; newly stored papers have `is_scout_seed = 0`

### 3. Per-Paper Citation Enrichment (user-triggered)
- On any paper's detail page, a **"Discover Citations"** button triggers 1-hop citation traversal for that specific paper
- Finds papers citing it + papers it cites, filters out duplicates already in DB
- User controls when to expand the graph — not automatic beyond the initial establishment

### Relevance Filtering
Human notes on existing papers steer what's considered relevant:
- When a user writes "the regime detection approach here is exactly what I need", future scouting prioritizes papers in that citation neighborhood
- When a user writes "too simplistic, not useful", the system de-prioritizes that branch
- Implementation: human notes are fed into the Haiku prompt when synthesizing newly discovered papers, and into the relevance scoring logic

## Semantic Scholar API

### Key endpoints
- `GET /paper/{paper_id}` — metadata (title, authors, abstract, year, venue, externalIds, citationCount, referenceCount)
- `GET /paper/{paper_id}/citations` — papers that cite this paper (paginated, up to 1000)
- `GET /paper/{paper_id}/references` — papers this paper cites (paginated, up to 1000)
- `GET /paper/search?query={query}` — keyword search (fallback for papers without Semantic Scholar IDs)

### Paper ID resolution
Semantic Scholar accepts multiple ID formats:
- Semantic Scholar ID: `649def34f8be52c8b66281af98ae884c09aef38b`
- arXiv ID: `arXiv:2403.09267`
- DOI: `DOI:10.1234/example`
- URL: `URL:https://arxiv.org/abs/2403.09267`

This means arXiv papers uploaded by the user can be resolved directly without a separate lookup.

### Rate limits
- Unauthenticated: 100 requests per 5 minutes
- With API key (free): 1 request/second sustained
- For scouting runs, implement rate limiting and backoff

## Workflows & Interface

See [workflow_and_interface.md](workflow_and_interface.md) for details on:
- Topic Establishment Workflow
- Structured Paper Note Format (including Human Note)
- Synthesis Workflow (3 tiers: bulk → deep → cross-paper)
- User Q&A Over Papers
- Topic Steering (direct + indirect via Human Notes)

### Implementation details retained here
- Paper-level fields (human_note, paper_info, abstract_excerpt) stored in `paper_notes`; per-(paper, topic) skim + deep synthesis stored in `topic_paper_notes`.
- `human_note` column on `paper_notes`: TEXT, nullable, default NULL — shared across topics.
- Human Note editable via htmx `PUT /papers/{id}/human-note`
- Topic skill prompts editable on the topic detail/edit page via upload or inline editor; stored in `topics.skim_skill_md` and `topics.deep_synthesis_skill_md`.
- Q&A uses vector similarity search over `paper_embeddings` + FTS5
- Steering changes tracked in `topic_steering_log` table
- Cross-paper synthesis triggered manually via a button on the topic detail page
- Synthesis is per-topic: triggered and displayed on the topic detail page and the paper detail page under a per-topic tab. No standalone Synthesis section.
- Dashboard has two cards: Topics and Papers. No standalone Synthesis card.

## Implementation Phases

See individual phase files for details:

1. [Phase 1: Foundation](phase1_foundation.md) — project skeleton, DB schema, topic CRUD
1b. [Phase 1b: Frontend, Paper Pages & Launcher](phase1b_frontend.md) — UI polish, paper list/detail pages, manual paper upload, desktop shortcut
1c. [Phase 1c: LLM Metadata Extraction](phase1c_llm_metadata.md) — Haiku extracts title, authors, abstract, venue, date from uploaded PDFs
2. [Phase 2: Scouting & Synthesis](phase2_scouting_synthesis.md) — Semantic Scholar citation graph, Haiku auto-summarization, structured notes
3. [Phase 3: Embeddings & Search](phase3_embeddings_search.md) — semantic ranking, relevance filtering, FTS5 + vector search
4. [Phase 4: Deep Synthesis, Q&A & Cross-Paper](phase4_deep_synthesis_qa.md) — Opus deep dives, Q&A, monthly cross-paper synthesis
5. [Phase 5: Steering & Enrichment](phase5_steering_enrichment.md) — human note feedback loop, per-paper citation enrichment, topic steering UI
6. [Phase 6: Scheduler & Launcher](phase6_scheduler_launcher.md) — Windows Task Scheduler, desktop shortcut launcher

## Problem-Centric Relevance (Core Design Principle)

Topics are not keyword buckets — they represent **practical problems the user is trying to solve** (e.g. "robust backtesting methodology for momentum strategies", "regime detection for portfolio allocation"). This fundamentally shapes how papers are ranked, recommended, and synthesized.

### How it works with citation-graph scouting

**1. Citation graph provides retrieval; keywords + embeddings provide filtering**
- **Retrieval**: The citation graph (Semantic Scholar) provides candidate papers — these are structurally related to the user's existing papers.
- **Keyword filter**: Candidate papers are filtered by topic keywords (title/abstract must contain at least one keyword). This keeps results focused even as the citation graph fans out.
- **Semantic ranking**: Filtered papers are scored by cosine similarity between paper embeddings and topic problem statement embeddings. This drives sort order and recommendation.

**2. Topic embeddings**
- Each topic's problem statements are embedded (all-MiniLM-L6-v2) and stored in `topic_embeddings`.
- When problem statements are updated (steering), topic embeddings are recomputed and all `topic_papers.relevance_score` values refreshed.
- Relevance score = max cosine similarity across the topic's problem statement embeddings.

**3. LLM structural skim (Phase 2) is problem-aware and skill-driven**
- Haiku prompt = `topic.skim_skill_md` (user's instructions) + paper metadata/abstract + topic's problem statements + keywords + up to 5 recent human notes from related papers + fixed JSON output schema.
- The user's skill defines what to extract and how; the system provides context and output format.
- If `skim_skill_md` is NULL → synthesis skipped; no fallback used.
- `skim_recommendation` field driven by the user's skill and problem-relevance.
- Same paper synthesised for a different topic uses that topic's skill → different output stored under that (paper, topic) row.

**4. Cross-paper synthesis is problem-framed**
- Monthly synthesis framed around problem statements: "What progress has been made? What gaps remain?"
- Human notes from paper annotations are fed into the prompt.

## Decoupled Architecture: Scouting vs Webapp

The paper scouting/synthesis pipeline and the webapp are **independent processes**. The webapp does NOT need to be running for scouting to work, and vice versa.

### Why
- Scouting runs on a schedule (Windows Task Scheduler) — it should work headlessly.
- The webapp is for browsing results, steering topics, and triggering manual actions.
- Both read/write the same SQLite database.

### How it works
- **`scheduler_entry.py`**: Standalone CLI script. Called by Task Scheduler. Imports scouting/synthesis services directly, writes to the DB, and exits.
- **`app.py`**: FastAPI webapp. Reads from the same DB. Can also trigger manual scouting/enrichment.
- **Shared layer**: `services/` and `db.py` are imported by both. No coupling to HTTP request context.

### Desktop Launcher
- **`scripts/launch_webapp.pyw`**: Starts uvicorn subprocess (no console), opens browser, stays alive to keep server running.
- **Desktop shortcut**: `.lnk` on Desktop pointing to `launch_webapp.pyw`.

## Per-Topic Skills & Per-(Paper, Topic) Synthesis (Core Design Principle)

Each topic owns two **skill prompts** uploaded/edited by the user:
1. **Structural Skim skill** (Haiku, Phase 2) — drives the structural skim. The user's skill defines what to extract and how; the system appends paper metadata and a fixed JSON output schema.
2. **Deep Synthesis skill** (Sonnet/Opus, Phase 4) — drives the deep synthesis pass.

Both skills are **required** (no built-in defaults) before scouting or any single-paper LLM pass can run for a topic. Skills are stored as Markdown in the `topics` table. Cross-paper synthesis (Phase 4) uses a bundled general skill and does not require a topic-specific upload.

Because a paper can belong to multiple topics and each topic frames the paper differently, all LLM outputs are **per (paper, topic) pair**:
- Storage: `topic_paper_notes` (UNIQUE on `topic_id, paper_id`).
- On the paper detail page, Structural Skim and Deep Synthesis sections render a tab per linked topic. Each tab's content is the output of that topic's skill on this paper. Tab order is driven by `topic_papers.relevance_score` (Phase 3) so the most-relevant topic appears first by default.
- When a topic's skill prompt changes, existing skim/deep rows remain but are flagged stale (via a hash comparison on `skim_skill_hash` / `deep_skill_hash`) so the user knows to regenerate. Relevance scores (Phase 3) are NOT invalidated by skill changes — only problem-statement edits recompute them.
- Papers linked to only one topic render without a tab bar (single section, same as before).

### How the phases stack
1. **Phase 2 (Haiku structural skim)** runs the topic's Structural Skim skill and writes `topic_paper_notes`. **Auto-fires on every paper arrival** — whether via scheduled scouting, manual Scout Now, per-paper "Discover Citations", or manual upload — once per (paper, topic) pair the paper is linked to. A **"Generate Structural Skim"** button on the paper detail page (per topic tab) lets the user re-run the skim manually for regeneration or retries.
2. **Phase 3 (embeddings)** scores each (paper, topic) pair against the topic's problem-statement embeddings — independent of skill prompts and LLM outputs — and writes `topic_papers.relevance_score`. Consumed to sort paper lists and order per-topic tabs.
3. **Phase 4 (Sonnet/Opus deep synthesis)** runs the topic's Deep Synthesis skill on a (paper, topic) pair and writes into the same `topic_paper_notes` row. **Never auto-fires** — it is always user-triggered from a **"Generate Deep Synthesis"** button on the paper detail page (per topic tab), after the user reviews the structural skim. Also powers Q&A and monthly cross-paper synthesis.

Both **"Generate Structural Skim"** and **"Generate Deep Synthesis"** buttons live on the paper detail page under each per-topic tab — the user can always trigger either pass manually regardless of prior state.

## Key Design Decisions
- **Citation-graph scouting over keyword-only search**: Papers are discovered via citation relationships from existing papers (Semantic Scholar API), not just keyword matching. Keywords filter the graph; they don't drive retrieval.
- **Semantic Scholar as primary API**: Covers arXiv, SSRN, PubMed, and more in a unified citation graph. Free tier sufficient for our volume.
- **User-controlled graph expansion**: 1-hop citations at establishment, then per-paper "Discover Citations" button. No automatic recursive crawling — user decides when to expand.
- **sqlite-vec over FAISS/ChromaDB**: Everything in one SQLite file, no separate process
- **Local embeddings over API**: all-MiniLM-L6-v2 is 22MB, runs in ms on CPU, free
- **htmx over React**: No build step, interactive enough for this use case
- **Decoupled scouting and webapp**: Scouting runs headlessly via Task Scheduler. Both share the same SQLite DB.
- **pypdf** for extracting full text from downloaded PDFs

## Verification
1. Run `pip install -e .` and start with `uvicorn poneglyph.app:app`
2. Create a topic with problem statements, upload initial papers
3. Verify citation discovery populates related papers
4. Verify Haiku synthesis generates structured notes
5. Search papers via keyword and semantic search
6. Approve a paper for deep synthesis, verify Opus output
7. Run cross-paper synthesis, verify research directions
8. Run `python poneglyph/scheduler_entry.py` manually to test scheduled flow
