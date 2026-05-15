# Phase 5: Steering & Enrichment

## Deliverables

### Topic Steering
- Steering log view showing direction changes over time on the topic detail page
- Wire problem context into LLM prompts and relevance scoring
- Human note aggregation and auto-suggest (lightweight LLM parses notes → suggests adjustments)

## Details

**Topic Steering** has two channels:

1. **Direct steering** (topic detail page): add/remove keywords, edit problem statements. All changes tracked in `topic_steering_log`. When problem statements change, topic embeddings are recomputed and relevance scores refreshed (Phase 3).

2. **Indirect steering** (via Human Notes): human notes on papers are aggregated on the topic steering page. A lightweight LLM call parses recent notes and suggests keyword/problem statement adjustments. Suggestions appear for user approval (not auto-applied). Feedback loop: read papers → annotate → steer → scout better papers.

## Status

Already implemented:
- Keywords and problem statements edit UI on topic detail page
- `topic_steering_log` written on keyword/PS saves

Not yet implemented:
- Steering log view (log is written but never displayed)
- Relevance re-scoring on steering changes
- Human note aggregation → LLM auto-suggest

## Dependencies
- Phase 2 (scouting pipeline)
- Phase 3 (embeddings for relevance re-scoring on steering changes)
