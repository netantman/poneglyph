# Phase 5: Steering & Enrichment

## Deliverables

### Topic Steering
- Keywords and problem statements UI on topic detail page
- Steering log view showing direction changes over time
- Wire problem context into LLM prompts and relevance scoring
- Human note aggregation and auto-suggest (lightweight LLM parses notes → suggests adjustments)

### Citation Enrichment UX
- Per-paper "Discover Citations" button (already wired in Phase 2) — this phase polishes the UX
- Citation graph visualization (optional): show how papers relate to each other within a topic
- Batch enrichment: select multiple papers and discover citations for all at once

### PDF Management
- PDF download manager (per-topic policy: 'link_only' or 'download')
- Settings page for configuring PDF policy and API keys

## Details

**Topic Steering** has two channels:

1. **Direct steering** (topic detail page): add/remove keywords, edit problem statements. All changes tracked in `topic_steering_log`. When problem statements change, topic embeddings are recomputed and relevance scores refreshed (Phase 3).

2. **Indirect steering** (via Human Notes): human notes on papers are aggregated on the topic steering page. A lightweight LLM call parses recent notes and suggests keyword/problem statement adjustments. Suggestions appear for user approval (not auto-applied). Feedback loop: read papers → annotate → steer → scout better papers.

**Citation enrichment** is the user-controlled mechanism for expanding the paper collection beyond automatic scouting. The user identifies interesting papers and clicks "Discover Citations" to explore their neighborhood. This phase improves the UX around this action (progress feedback, batch operations, provenance display).

## Dependencies
- Phase 2 (scouting pipeline, citation discovery)
- Phase 3 (embeddings for relevance re-scoring on steering changes)
