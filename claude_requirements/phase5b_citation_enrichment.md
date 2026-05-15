# Phase 5b: Citation Enrichment UX

## Deliverables

### Citation Enrichment UX
- Per-paper "Discover Citations" button — already wired in Phase 2; this phase polishes the UX
- Citation graph visualization (optional): show how papers relate to each other within a topic
- Batch enrichment: select multiple papers and discover citations for all at once

## Details

**Citation enrichment** is the user-controlled mechanism for expanding the paper collection beyond automatic scouting. The user identifies interesting papers and clicks "Discover Citations" to explore their neighborhood.

This phase improves the UX around this action:
- Progress feedback during discovery
- Batch operations (select N papers, discover citations for all)
- Provenance display (which seed paper led to which discovered paper)

## Status

Already implemented:
- Per-paper "Discover Citations" button (single paper, per-topic context)

Not yet implemented:
- Batch citation discovery across multiple selected papers
- Citation graph visualization (optional)

## Dependencies
- Phase 2 (scouting pipeline, citation discovery via Semantic Scholar)
- Phase 3 (embeddings — new papers from citation discovery need embeddings)
