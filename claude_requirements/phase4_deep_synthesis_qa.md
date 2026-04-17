# Phase 4: Deep Synthesis (Sonnet/Opus), Q&A & Cross-Paper Synthesis

## Scope
Phase 4 covers the **deep synthesis pass** (Sonnet or Opus) run against a paper in the context of a specific topic using that topic's Deep Synthesis skill, plus Q&A and cross-paper synthesis.

Structural skim (Haiku) is in Phase 2.

### Trigger policy
Deep synthesis **never auto-fires**. It is always user-triggered from a **"Generate Deep Synthesis"** button on the paper detail page, inside the per-topic tab the user is viewing. There is no scheduled or on-discovery path — Phase 2's Haiku structural skim is the only LLM pass that runs automatically for incoming papers. The user reads the skim, decides whether deeper analysis is worthwhile (typically when `skim_recommendation == 'deep_dive'`), and clicks the button to spend Sonnet/Opus cost.

## Deliverables

### Per-topic Deep Synthesis skill
- Upload/edit UI on the topic edit page — file upload (`.md`) and inline editor. **Required before deep synthesis can run.** Stored in `topics.deep_synthesis_skill_md`. No built-in default — each topic must supply its own skill.
- Topic Detail page shows a "Deep skill: ✓ Custom" / "✗ None" badge.
- Generate Deep Synthesis is disabled with a tooltip ("Upload a Deep Synthesis skill first") when `deep_synthesis_skill_md` is NULL.

### Deep Synthesis (Sonnet)
- `services/llm_deep.py`: deep synthesis service
  - `extract_pdf_text(path) → (text | None, error_msg | None)` — reads local PDF via pypdf; returns `(None, reason)` on any failure
  - `deep_synthesize(paper, topic, pdf_text, related_notes)` → Markdown string
  - Uses Claude Sonnet (latest — `claude-sonnet-4-6` as of 2026-04; always use the latest Sonnet available in `config.py`) — model identifier recorded in `deep_synthesis_model_used`
  - Prompt: topic's `deep_synthesis_skill_md` + paper metadata/abstract + full PDF text (capped at 80k chars) + topic problem statements + up to 3 related human notes
  - **PDF is strictly required — no abstract-only fallback.** The run is blocked (error toast, `HX-Reswap: none`) when any gate fails:
    1. `paper.pdf_local_path` is not set
    2. `pdf_local_path` is a remote URL (not a local file)
    3. The file does not exist on disk
    4. pypdf cannot extract any text (encrypted, image-only, or corrupt)
- Results stored **per (paper, topic)** in `topic_paper_notes` (columns: `deep_synthesis`, `deep_synthesis_model_used`, `deep_skill_hash`, `deep_generated_at`)
- Routes:
  - `GET /papers/{id}/deep-synthesis?topic_id={tid}` — return partial for tab switching
  - `POST /papers/{id}/deep-synthesis` with `topic_id` — run synthesis synchronously; return updated partial on success, or error toast on gate failure

### Deep Synthesis UI on paper detail
- [x] **Per-topic tabbed Deep Synthesis section** below Structural Skim — same tab layout as Structural Skim (one tab per linked topic; no tab bar if only one topic); tab switching via `GET /papers/{id}/deep-synthesis?topic_id=`
- [x] Each tab shows rendered deep synthesis (Markdown → HTML via `markdown` lib + `md` Jinja filter) or empty state with Generate button
- [x] Generate button triggers `POST /papers/{id}/deep-synthesis`; spinner while running; swaps tab panel on completion
- [x] Footer: last run timestamp, model used; "⟳ Skill updated since last run" badge when `deep_skill_hash` mismatches current skill hash
- [ ] View skill modal in footer (shows the exact prompt used)
- [ ] Model toggle on Generate button (Sonnet / Opus) with cost hint

### Q&A
- "Ask about papers" input on the search page
- Backed by vector similarity search over paper embeddings (Phase 3) + FTS5
- Top-N matching papers' structural skim + human notes fed into a Sonnet call that answers with citations
- Answers include inline paper citation hyperlinks (Markdown links → paper detail pages)

### Cross-Paper Synthesis
- `services/llm_cross.py`: cross-paper synthesis service
- Uses a **bundled general skill** (`claude_requirements/skill_multi_paper_synthesis.md`) — no per-topic upload needed for cross-paper synthesis
- Consumes all `topic_paper_notes` rows for a topic (not `paper_notes`) plus each paper's human note
- Framed around the topic's problem statements: "What progress has been made? What gaps remain?"
- Output: narrative synthesis + `research_directions` (JSON list)
- Stored in `cross_syntheses` (topic_id, paper_ids, synthesis, research_directions, model_used, created_at)
- **User-triggered only** — a **"Synthesize Topic"** button on the topic detail page fires the run (HTMX, same polling pattern as Scout Now); never scheduled or automatic
- Displayed on the topic detail page as a collapsible "Latest Cross-Paper Synthesis" section; previous runs are replaced on re-run

## Details

**Deep synthesis** is user-approved per (paper, topic) pair — the user reviews the structural skim in the relevant topic tab and, if marked `deep_dive`, clicks Generate Deep Synthesis. Sonnet/Opus expands the analysis using the full paper text (if PDF available) and the topic's Deep Synthesis skill.

Because deep syntheses are per (paper, topic), the same paper framed under two different topics will produce two distinct deep syntheses tailored to each topic's skill and problem context.

**Q&A** lets users ask questions like "which papers mention backtesting for time-series?" — answered via vector similarity search over paper notes + abstracts, refined by a Sonnet call.

**Cross-paper synthesis** is user-triggered — a "Synthesize Topic" button on the topic detail page fires the run. It is never scheduled or automatic. Synthesizes across all papers in a topic, framed around the topic's problem statements: "What progress has been made toward solving [problem]? What gaps remain?" Produces actionable research directions. Human Notes from paper annotations are fed into the prompt so Sonnet/Opus understands which directions the user values.

## Dependencies
- Phase 1 foundation (with `topic_paper_notes` table and `topics.deep_synthesis_skill_md` column)
- Phase 2 (structural skim exists per (paper, topic); recommendation drives which papers surface for deep dive)
- Phase 3 (embeddings for Q&A vector search)
