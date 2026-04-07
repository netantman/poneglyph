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
- [ ] `services/semantic_scholar.py`: API client with rate limiting and backoff
  - `resolve_paper_id(identifier: str) -> str | None` — resolve arXiv ID, DOI, or URL to Semantic Scholar paper ID
  - `get_paper(paper_id: str) -> dict | None` — fetch paper metadata (title, authors, abstract, year, venue, externalIds, citationCount)
  - `get_citations(paper_id: str, limit: int = 500) -> list[dict]` — papers that cite this paper (paginated)
  - `get_references(paper_id: str, limit: int = 500) -> list[dict]` — papers this paper cites (paginated)
  - Rate limiter: respect 100 req/5min (unauthenticated) or 1 req/sec (with API key)
- [ ] `services/citation_scout.py`: Citation graph traversal
  - `discover_from_paper(paper_id: int, topic_id: int) -> list[int]` — 1-hop both directions, returns new paper IDs
  - `establish_topic(topic_id: int) -> list[int]` — run discovery for all papers in topic
  - `scout_topic(topic_id: int) -> list[int]` — run discovery for all papers in topic (ongoing scouting)
  - Dedup: skip papers already in DB (by semantic_scholar_id or title fuzzy match)
  - Keyword filter: if topic has keywords, only keep papers whose title/abstract matches at least one
  - Store `paper_citations` rows to track the graph

### Haiku bulk synthesis
- [ ] `services/llm_bulk.py`: Haiku structured note generation
  - Input: paper (title, abstract, authors, venue, year) + topic (problem statements) + human notes from related papers
  - Output: structured JSON with key_insights, trading_applications, recommendation
  - Prompt template includes: "How does this paper contribute to solving these problems?"
  - `recommendation` field: 'read' | 'skip' | 'deep_dive' — driven by problem-relevance
- [ ] Store results in `paper_notes` table
- [ ] Handle papers with minimal metadata gracefully (some Semantic Scholar entries have no abstract)

### Pipeline orchestration
- [ ] `pipeline.py`: Wires discovery + synthesis together
  - `run_establishment(topic_id)`: for each paper in topic → discover citations/references → synthesize new papers
  - `run_scouting(topic_id)`: for all papers in topic → discover new citations → filter → synthesize
  - `run_paper_enrichment(paper_id, topic_id)`: single-paper citation discovery + synthesis
  - Logging: record scout runs in `scout_runs` table

### UI integration
- [ ] "Scout Now" button on topic detail page — triggers `run_scouting(topic_id)` via htmx
- [ ] "Discover Citations" button on paper detail page — triggers `run_paper_enrichment(paper_id, topic_id)` via htmx
- [ ] Show discovery provenance on paper detail: "Discovered via: [source paper title] (citation/reference)"
- [ ] Progress indicator during scouting (htmx polling or SSE)

### Semantic Scholar ID resolution for existing papers
- [ ] On manual paper upload (Phase 1b already done), attempt to resolve Semantic Scholar ID:
  - arXiv URL → `arXiv:{id}` lookup
  - Other URL → `URL:{url}` lookup
  - Title-based search as fallback
- [ ] Retroactively resolve IDs for papers already in DB that lack `semantic_scholar_id`

## Details

### Why Semantic Scholar?
- Unified citation graph covering arXiv, SSRN, PubMed, ACL, and more
- Free API with generous rate limits
- Accepts arXiv IDs, DOIs, and URLs directly — no separate ID resolution needed for most papers
- Returns structured metadata including external IDs (arXiv, DOI, MAG, PubMed)

### Citation discovery flow
```
Paper (user uploads or previously discovered)
  → Resolve Semantic Scholar ID
  → GET /paper/{id}/citations  → papers that cite it
  → GET /paper/{id}/references → papers it cites
  → Filter: already in DB? keyword match?
  → Store new papers
  → Run Haiku synthesis on each
  → Repeat for ongoing scouting (new citations of all topic papers)
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
