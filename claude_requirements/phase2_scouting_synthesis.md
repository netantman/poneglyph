# Phase 2: Scouting & Synthesis

## Deliverables
- Semantic Scholar API service (citation/reference lookup, metadata, ID resolution)
- Citation graph traversal logic (1-hop, both directions)
- Haiku bulk synthesis service (structured notes for every discovered paper)
- Pipeline: initial papers → resolve IDs → fetch citations/references → filter → store → synthesize
- "Scout Now" button on topic detail page
- "Discover Citations" button on paper detail page

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

### Haiku bulk synthesis
- [x] `services/llm_bulk.py`: Haiku structured note generation
  - `synthesize_paper(paper, topic, related_notes)` → key_insights, trading_applications, recommendation
  - HTML stripping via `_HTMLStripper` (drops `<img>`, `<figure>`, `<svg>`)
  - Recommendation: read | skip | deep_dive
- [x] Results stored in `paper_notes` (key_insights, trading_applications, recommendation, model_used)
- [x] Also stored in `topic_papers.recommendation` for list view

### Pipeline orchestration
- [x] `pipeline.py`: wires discovery + synthesis
  - `run_paper_enrichment(paper_id, topic_id, run_id)` — single paper
  - `run_topic_scout(topic_id, run_id)` — seed papers only (`topic_papers.is_scout_seed = 1`)
  - `MAX_SYNTH_PER_RUN = 30` — caps Haiku cost per run
  - Scout run lifecycle in `scout_runs` table (started_at, finished_at, status, error_message)
  - `_synthesize_paper`: fetches up to 5 most-recently-updated human notes from other topic
    papers as context for Haiku; excludes the paper being synthesized to avoid self-reference
  - If no seed papers are configured for a topic, `run_topic_scout` logs a warning and exits
    with `status = 'no_seeds'` rather than doing nothing silently

### UI integration
- [x] "Scout Now" button on topic detail page — `POST /scout/topic/{id}`, starts background task, HTMX polls `/scout/run/{run_id}` every 3s; shows prominent styled message box "Scouting in progress" while running, success/error box when done
- [x] "Discover Citations" button on paper detail page — topic dropdown if multiple, direct POST if one
- [x] "Synthesize" button on paper detail page — on-demand single-paper synthesis
- [x] Key Insights shown as bullet list; recommendation badge (⭐ Deep Dive / 📖 Read) in header
- [x] Progress indicator: live polling with spinner until `finished_at` set

### Semantic Scholar ID resolution for existing papers
- [x] `discover_from_paper` resolves and back-fills `semantic_scholar_id` on first scout
- [ ] Retroactive bulk resolution for all existing papers (not yet implemented — triggered automatically on first scout)

## Details

### Why Semantic Scholar?
- Unified citation graph covering arXiv, SSRN, PubMed, ACL, and more
- Free API with generous rate limits
- Accepts arXiv IDs, DOIs, and URLs directly — no separate ID resolution needed for most papers
- Returns structured metadata including external IDs (arXiv, DOI, MAG, PubMed)

### Citation discovery flow
```
Topic Scout Now / scheduled job
  → Fetch papers with is_scout_seed = 1 for this topic
  → If none: log warning, exit with status='no_seeds'
  → For each seed paper:
      → Resolve Semantic Scholar ID
      → GET /paper/{id}/citations  → papers that cite it
      → GET /paper/{id}/references → papers it cites
      → Filter: already in DB? keyword/problem-statement term match on title+abstract?
      → Store new papers (linked to topic; is_scout_seed = 0 by default)
      → Run Haiku synthesis on each (prompt includes: topic name, problem statements,
        keywords, and up to 5 recent human notes from other papers in the topic)
```

### Handling papers without abstracts
Some Semantic Scholar entries have minimal metadata (no abstract, no venue). For these:
- Store what's available (title, authors, year, external IDs)
- If an arXiv ID exists, fetch full metadata from arXiv API (existing `arxiv_fetch.py`)
- Haiku synthesis works from whatever is available; notes will be less detailed but still useful
- Flag these papers for user review ("incomplete metadata")

### Manual upload synthesis
Papers uploaded manually (including arXiv URL uploads) also go through Haiku synthesis:
- arXiv URL uploads: LLM refines/supplements the API-fetched metadata
- PDF uploads: LLM uses extracted PDF text
- Manual entry: LLM works from provided title + abstract
- This ensures manually uploaded papers have the same structured notes as discovered papers

## Dependencies
- Phase 1b (paper list/detail pages, manual upload)
- Anthropic API key configured (for Haiku synthesis)
- Semantic Scholar API access (free, no key required for basic usage)
