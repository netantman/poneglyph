# Phase 3: Embeddings & Search

## Role in the architecture
Phase 3 provides the **relevance scoring** that links the citation-graph retrieval (Phase 2) to the per-topic LLM passes (Phase 2 Haiku skim, Phase 4 Sonnet/Opus deep synthesis). It is **orthogonal to the per-topic skills refactor** — nothing in Phase 3 depends on skill prompts or the new `topic_paper_notes` table. Its outputs are numeric scores on `topic_papers.relevance_score`, consumed by:

- **Phase 2**: deprioritises/filters low-relevance citation candidates before a Haiku skim is spent on them; also drives the default sort order within each topic's paper list.
- **Phase 4**: tab ordering on the paper detail page (tabs ordered by `relevance_score DESC`), and surfacing papers for Q&A and cross-paper synthesis.
- **Phase 5 (steering)**: when the user edits problem statements, topic embeddings are recomputed and all `topic_papers.relevance_score` values refreshed — skills themselves are unaffected.

## Deliverables
- Paper embedding generation with sentence-transformers (title + abstract)
- Topic embedding generation from problem statements (recomputed on steering changes)
- Relevance scoring: max cosine similarity of paper embedding vs topic's problem statement embeddings → stored as `topic_papers.relevance_score`
- Search page: keyword (FTS5) + semantic (vector) search
- Paper lists default-sorted by relevance score (problem-similarity), not date

## Manual relevance recalculation

- [x] **Recalculate Relevance** button in the topic detail page actions bar — `POST /topics/{id}/recalculate-relevance`; calls `update_topic_relevance_scores`, returns the refreshed papers list partial (swaps `#topic-papers-list`) with a toast showing how many papers were updated

## Paper sort order in topic detail page

After each scouting run (manual Scout Now or periodic scheduler), relevance scores are updated and the topic paper list re-sorts by:

1. **Read Next** papers float to top (`p.read_next DESC`)
2. **Not Interesting** papers sink to bottom (`tp.not_interesting ASC`)
3. **Relevance score** descending (`tp.relevance_score DESC`) — papers without a score yet are treated as the upper bound (1.0) so unsynthesised papers don't unfairly sink
4. **Latest published date** first (`p.published_date DESC`)
5. **created_at** as tiebreaker (`p.created_at DESC`)

Papers without a relevance score (`topic_papers.relevance_score IS NULL`) are assigned an effective score of 1.0 (upper bound) in the ORDER BY, e.g. `COALESCE(tp.relevance_score, 1.0) DESC`.

## Details
Embeddings serve two purposes in the citation-graph workflow:

1. **Relevance filtering**: When the citation graph returns many candidates, embeddings score them against the topic's problem statements. High-relevance papers surface first; low-relevance papers from distant citation branches get filtered or deprioritized.

2. **Search**: Users can search across all papers via keyword (FTS5) or semantic similarity (vector search). This is independent of the citation graph — useful for finding connections across topics.

Topic embeddings are recomputed whenever problem statements are updated (via steering), and all existing relevance scores for that topic are refreshed. This makes steering immediately effective.

### Relationship to per-topic skills and `topic_paper_notes`
Phase 3 does NOT read or write `topic_paper_notes` or the topic skill prompts. Relevance scoring embeds the topic's **problem statements** (not its skill prompt) against the paper's title + abstract. The scoring is deliberately independent of the LLM passes so that:

- A topic can be re-scored without re-running any Haiku/Sonnet call.
- Changing a skill prompt does NOT invalidate relevance scores (skill-hash staleness on skim/deep is tracked separately on `topic_paper_notes`).
- The tab ordering on the paper detail page (Phase 4 UI) can use `relevance_score` immediately without waiting for skim/deep to be regenerated.

## Dependencies
- Phase 2 (papers in the database; `topic_papers` rows exist for scoring to target)

## Consumed by
- Phase 2 (candidate deprioritisation during citation scouting; topic paper list sort)
- Phase 4 (tab order on the paper detail page; Q&A retrieval; cross-paper synthesis paper selection)
- Phase 5 (problem-statement steering re-scoring loop)
