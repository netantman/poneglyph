# Phase 3: Embeddings & Search

## Deliverables
- Paper embedding generation with sentence-transformers (title + abstract)
- Topic embedding generation from problem statements (recomputed on steering changes)
- Relevance scoring: max cosine similarity of paper embedding vs topic's problem statement embeddings → stored as `topic_papers.relevance_score`
- Search page: keyword (FTS5) + semantic (vector) search
- Paper lists default-sorted by relevance score (problem-similarity), not date

## Details
Embeddings serve two purposes in the citation-graph workflow:

1. **Relevance filtering**: When the citation graph returns many candidates, embeddings score them against the topic's problem statements. High-relevance papers surface first; low-relevance papers from distant citation branches get filtered or deprioritized.

2. **Search**: Users can search across all papers via keyword (FTS5) or semantic similarity (vector search). This is independent of the citation graph — useful for finding connections across topics.

Topic embeddings are recomputed whenever problem statements are updated (via steering), and all existing relevance scores for that topic are refreshed. This makes steering immediately effective.

## Dependencies
- Phase 2 (papers in the database with structured notes)
