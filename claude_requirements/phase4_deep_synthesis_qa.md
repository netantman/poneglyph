# Phase 4: Deep Synthesis, Q&A & Cross-Paper Synthesis

## Deliverables

### Deep Synthesis
- Review page with approve/skip buttons
- Opus deep synthesis (with PDF text extraction via pypdf)

### Q&A
- "Ask about papers" feature: vector search + LLM answer with citations
- Available via an input on the search page

### Cross-Paper Synthesis (Monthly)
- Cross-paper synthesis service consuming all paper notes per topic
- Separate scheduler entry for monthly cross-paper runs
- Cross-synthesis view with research directions and theme clusters

## Details

**Deep synthesis** is user-approved — the user reviews bulk notes and selects papers for Opus-level analysis. Opus expands all sections using the full paper text (if PDF available).

**Q&A** lets users ask questions like "which papers mention backtesting for time-series?" — answered via vector similarity search over paper notes + abstracts, optionally refined by an LLM call.

**Cross-paper synthesis** runs monthly (less frequently than weekly scouting). It is NOT user-triggered — it runs automatically via scheduler. Opus synthesizes across all papers in a topic, framed around the topic's problem statements: "What progress has been made toward solving [problem]? What gaps remain?" Produces actionable research directions, not generic theme summaries.

Human Notes from paper annotations are fed into the cross-paper synthesis prompt so Opus understands which directions the user values.

## Dependencies
- Phase 2 (bulk notes exist for papers — Haiku synthesis)
- Phase 3 (embeddings for Q&A vector search)
