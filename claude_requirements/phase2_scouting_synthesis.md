# Phase 2: Scouting & Structural Skim (Haiku)

## Scope
Phase 2 covers everything up to and including the **Haiku structural skim**. Deep synthesis (Sonnet/Opus) is handled in Phase 4.

### Trigger policy
The Haiku structural skim **auto-fires for every paper entering the system**, once per (paper, topic) pair:
- Scheduled scouting (weekly Task Scheduler run)
- Manual "Scout Now" on a topic
- Per-paper "Discover Citations" enrichment
- Manual upload (URL, PDF upload, PDF link, manual entry) — runs once per topic the paper is linked to at upload time
- **"Generate Structural Skim"** button on the paper detail page (per topic tab) — on-demand regeneration or first-time run if the paper was added before the topic had a skill

## Deliverables
- Semantic Scholar API service (citation/reference lookup, metadata, ID resolution)
- Citation graph traversal logic (1-hop, both directions)
- **Per-topic Structural Skim skill**: upload/edit UI on the topic edit page; stored in `topics.skim_skill_md`
- Haiku structural skim service that runs the topic's skill against each paper (Pass 1 + Pass 2)
- **Per-(paper, topic) skim storage** in `topic_paper_notes`
- Pipeline: initial papers → resolve IDs → fetch citations/references → filter → store → run skim
- "Scout Now" button on topic detail page (disabled until skim skill is uploaded)
- "Discover Citations" button on paper detail page
- Structural Skim tabbed UI on the paper detail page (one tab per linked topic)

## TODO

### Semantic Scholar integration
- [x] `services/semantic_scholar.py`: API client with rate limiting (1 req/sec, `asyncio.Lock`)
  - `get_paper(identifier)` — fetch by arXiv ID, DOI, URL, or bare S2 ID
  - `resolve_to_s2_id(identifier)` → bare S2 hex ID
  - `get_citations(s2_id, limit)` / `get_references(s2_id, limit)` — paginated
  - Optional `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher limits
- [x] `services/citation_scout.py`: Citation graph traversal
  - `discover_from_paper(paper_id, topic_id)` → list of paper IDs **newly associated with the topic**
    (includes both papers new to the DB and papers already in the DB that weren't yet linked to
    this topic; papers already associated with the topic are skipped entirely)
  - 1-hop both directions (papers that cite the seed AND papers the seed cites); dedup by S2 ID,
    (source, source_id), and title
  - Keyword filter: topic keywords/priority_keywords **and significant words extracted from
    problem statements** (words >4 chars, lowercased) — substring match on title+abstract
  - If no keywords and no problem statements → keep all (no filter)
  - `_link_to_topic` returns `bool` — True when a new topic association was created, False if
    already linked; `new_ids` is driven by this return value, not by DB-insert status
  - Stores `paper_citations` and `topic_papers` rows; back-fills `semantic_scholar_id`

### Per-topic Structural Skim skill
- [x] Topic edit page gains a **Structural Skim skill** field — file upload (`.md`) and inline editor (textarea). **Required before scouting or structural skim can run.** Stored in `topics.skim_skill_md` (TEXT, nullable). No built-in default — each topic must supply its own skill.
- [x] Topic Detail page shows a small "Skim skill: ✓ Custom" / "✗ None" badge.
- [x] Scout Now + Generate Structural Skim actions are **disabled with a tooltip** ("Upload a Structural Skim skill first") when `skim_skill_md` is NULL.
- [x] If `skim_skill_md` is NULL when synthesis is attempted programmatically, log a warning and skip — do not use any fallback prompt.
- [x] Validation on upload: reject blank/whitespace; show preview before save.

### Haiku structural skim
- [x] `services/llm_bulk.py`: Haiku structured note generation
  - `synthesize_paper(paper, topic, related_notes)` runs the topic's Structural Skim skill via claude-haiku-4-5
  - HTML stripping via `_HTMLStripper` (drops `<img>`, `<figure>`, `<svg>`)
  - **If `topic.skim_skill_md` is None or empty → return `{}` immediately** (no fallback prompt)
  - **PDF-aware paper text extraction** (`extract_skim_sections`):
    - If `paper.pdf_local_path` is set, is a local file, and pypdf can extract text → extract **first 8 000 chars + last 4 000 chars** of the full PDF text, labelled `[Opening]` and `[Closing]`. This head+tail approach is robust to OCR artifacts and non-academic document structures (e.g. bank research reports) that do not follow standard Abstract/Introduction/Conclusion anatomy.
    - If no PDF or extraction fails → fall back to `paper.abstract` only. The prompt explicitly notes `[Source: abstract only]` so the skim fields can flag gaps accordingly.
    - Extraction caps at ~12 000 chars to keep Haiku token cost low.
- [x] Prompt assembly uses the topic's `skim_skill_md` as the skill/task instructions, followed by: paper metadata + extracted paper text (PDF sections or abstract-only), topic name, problem statements, keywords, up to 5 recent human notes from other papers in the topic, and the fixed JSON output schema. The user's skill defines what to extract — the system only appends paper context and output format.
- [x] The structural skim extracts the following fields (the user's skill determines methodology; the output JSON schema is fixed):
  - `main_claim`, `data_source`, `strategy_type`, `headline_statistic`
  - `signal_mechanism`, `data_details`, `sample`, `universe`, `portfolio_construction`
  - `key_tables` (list), `key_metrics`
  - `skim_recommendation`: read | skip | deep_dive
- [x] Results stored **per (paper, topic)** in `topic_paper_notes`:
  - All skim fields, `skim_recommendation`, `skim_model_used`, `skim_skill_hash` (SHA-256 of the skill used), `skim_generated_at`
- [x] Also stored in `topic_papers.recommendation` for list view (mirrors `skim_recommendation`)
- [x] Running skim for paper X against topic A does **not** touch topic B's row — each tab is independent.

### Pipeline orchestration
- [x] `pipeline.py`: wires discovery + synthesis
  - `run_paper_enrichment(paper_id, topic_id, run_id)` — single paper
  - `run_topic_scout(topic_id, run_id)` — seed papers only (`topic_papers.is_scout_seed = 1`)
  - No cap on Haiku synthesis per run — all newly discovered papers are synthesized
  - Scout run lifecycle in `scout_runs` table (started_at, finished_at, status, error_message)
  - `_synthesize_paper`: fetches up to 5 most-recently-updated human notes from other topic
    papers as context for Haiku; excludes the paper being synthesized to avoid self-reference
  - If no seed papers are configured for a topic, `run_topic_scout` logs a warning and exits
    with `status = 'no_seeds'` rather than doing nothing silently

### UI integration
- [x] "Scout Now" button on topic detail page — `POST /scout/topic/{id}`, starts background task, HTMX polls `/scout/run/{run_id}` every 3s; shows prominent styled message box "Scouting in progress" while running, success/error box when done
- [x] Scout Now is disabled when `skim_skill_md` is NULL (tooltip: "Upload a Structural Skim skill first")
- [x] "Discover Citations" button on paper detail page — topic dropdown if multiple, direct POST if one
- [x] "Generate Structural Skim" button on paper detail page — on-demand skim for the currently active topic tab (topic dropdown if multiple, direct POST if one)
- [x] **Per-topic tabbed Structural Skim section** on paper detail page:
  - Tab bar appears only when paper is linked to ≥2 topics (single-topic papers render as before)
  - One tab per linked topic, ordered by `topic_papers.relevance_score DESC, topics.name`
  - Tab state preserved in URL (`?topic={topic_id}`); default is the highest-relevance topic
  - Tab content swapped via htmx (`GET /papers/{id}/structural-skim?topic_id={tid}` returns the partial)
  - Each tab shows: skim fields (reuse existing `papers/partials/structural_skim.html`), recommendation badge, `Regenerate with {topic name}'s skill` button, footer with `Last run (NYC time) / model / View skill ↗`
  - `Last run` timestamp displayed in NYC local time (America/New_York) via the `nyc` Jinja2 filter
  - `View skill ↗` opens a modal rendering the skill as formatted HTML (markdown rendered, not raw text) — same visual style as Claude chat previews
  - "⟳ Skill updated since last run" badge when `topic_paper_notes.skim_skill_hash` ≠ current hash of `topics.skim_skill_md`
- [x] Structural skim shown on paper detail: main claim, signal mechanism, sample, key metrics; recommendation badge (⭐ Deep Dive / 📖 Read / ⏭ Skip) in header
- [x] Progress indicator: live polling with spinner until `finished_at` set

### Semantic Scholar ID resolution for existing papers
- [x] `discover_from_paper` resolves and back-fills `semantic_scholar_id` on first scout
- [x] Retroactive bulk resolution for all existing papers — `resolve_missing_s2_ids()` in `pipeline.py`, called automatically at the start of every `run_topic_scout`

### PDF reference extraction fallback (for seed papers not indexed by S2)
Bank research reports and grey-literature PDFs are not in the Semantic Scholar graph. When a seed paper yields 0 references from S2 and has a local PDF with no prior citation data, fall back to Haiku-based reference extraction from the PDF text.

- [x] `services/llm_refs.py`: `extract_references_from_pdf(pdf_path) → list[dict]`
  - Extracts full PDF text (no character cap, via pypdf directly); locates the reference section by searching for the last "References" / "Bibliography" / "Works Cited" header, falling back to the last 30 000 chars if no header found
  - Single Haiku call returns JSON array of `{title, authors, year, venue}`
  - Returns `[]` on failure or when no reference list is found
- [x] `semantic_scholar.search_paper(title, limit=3) → dict | None`
  - Uses S2 `/paper/search?query=` endpoint to resolve a reference by title
  - Returns top result or None
- [x] Title similarity guard in `citation_scout.py`: normalized token-overlap ≥ 0.7 required before accepting an S2 match, preventing false positives on short/generic titles
- [x] Fallback integrated into `discover_from_paper`:
  - Condition: S2 returned 0 references **AND** no `paper_citations` rows with `direction='cites'` exist yet for this paper (idempotency guard) **AND** paper has `pdf_local_path` set
  - For each extracted reference: search S2 by title → similarity check → keyword filter → upsert paper → link to topic → record `paper_citations` row (direction=`cites`)
  - Discovered papers flow into the same skim pipeline as S2-sourced papers
  - Logged distinctly: `"pdf_refs fallback: paper={id} extracted={n} resolved={m} new={k}"`

## Details

### Why Semantic Scholar?
- Unified citation graph covering arXiv, SSRN, PubMed, ACL, and more
- Free API with generous rate limits
- Accepts arXiv IDs, DOIs, and URLs directly — no separate ID resolution needed for most papers
- Returns structured metadata including external IDs (arXiv, DOI, MAG, PubMed)

### Citation discovery flow
```
Topic Scout Now / scheduled job
  → Verify topic has a Structural Skim skill (skim_skill_md NOT NULL) — skip synthesis if missing
  → Fetch papers with is_scout_seed = 1 for this topic
  → If none: log warning, exit with status='no_seeds'
  → For each seed paper:
      → Resolve Semantic Scholar ID
      → GET /paper/{id}/citations  → papers that cite it
      → GET /paper/{id}/references → papers it cites
      → [Fallback if S2 references = 0 AND no prior paper_citations AND pdf_local_path set]:
          → extract_pdf_text → last 20k chars → Haiku → structured reference list
          → For each reference: S2 title search → similarity ≥ 0.7 → treat as S2 paper
      → Filter: already in DB? keyword/problem-statement term match on title+abstract?
      → Store new papers (linked to topic; is_scout_seed = 0 by default)
      → Run Haiku Structural Skim on each (only if skim_skill_md is set):
        Prompt = topic.skim_skill_md (user's skill/instructions)
                 + paper metadata/abstract + topic problem statements + keywords
                 + up to 5 recent human notes from other papers in the topic
                 + fixed JSON output schema
        → Write one row to topic_paper_notes (UNIQUE topic_id, paper_id); tag with skim_skill_hash
```

### Handling papers without abstracts
Some Semantic Scholar entries have minimal metadata (no abstract, no venue). For these:
- Store what's available (title, authors, year, external IDs)
- If an arXiv ID exists, fetch full metadata from arXiv API (existing `arxiv_fetch.py`)
- Haiku synthesis works from whatever is available; notes will be less detailed but still useful
- Flag these papers for user review ("incomplete metadata")

### Manual upload synthesis
Papers uploaded manually (including arXiv URL uploads) go through the Haiku structural skim **once per topic they are linked to that has a Structural Skim skill set**, using each topic's own skill:
- arXiv URL uploads: LLM refines/supplements the API-fetched metadata
- PDF uploads: LLM uses extracted PDF text (reads abstract, intro contribution sentences, conclusion, then data/methodology)
- Manual entry: LLM works from provided title + abstract (some Pass 2 fields may be *not found*)
- If the paper is linked to N topics at upload time, N skim runs fire (one per topic) and N `topic_paper_notes` rows are created.

## Dependencies
- Phase 1b (paper list/detail pages, manual upload)
- Anthropic API key configured (for Haiku synthesis)
- Semantic Scholar API access (free, no key required for basic usage)
