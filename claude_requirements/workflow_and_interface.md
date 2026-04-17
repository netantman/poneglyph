# Workflow & Interface

This document describes the user-facing workflows: topic establishment, paper discovery, synthesis, Q&A, and steering.

## Topic Establishment Workflow

The core workflow starts with the user seeding a topic:

### Step 1: Create Topic
- User creates a topic with name, description, and problem statements
- Keywords are optional but recommended — they filter the citation graph to keep results focused
- Problem statements drive relevance scoring and LLM synthesis prompts

### Step 1b: Upload Skill Prompts (required before scouting/LLM reading)
Each topic owns two skill prompts that define exactly how the LLM reads papers in this topic's context:
- **Structural Skim skill** (for Haiku, Phase 2) — defines what Pass 1 / Pass 2 extract and how recommendations are assigned.
- **Deep Synthesis skill** (for Sonnet/Opus, Phase 4) — defines the structure of deep analysis after the user approves.

Until both skills are set, Scout Now, Generate Structural Skim, and Generate Deep Synthesis are disabled for this topic with a hint: "Upload topic skills first". Skills can be uploaded as `.md` files or edited inline on the Topic Edit page; the Topic Detail page shows a "Skills ✓" badge when both are present.

### Step 2: Upload Initial Papers
- User uploads a few papers that represent the core of the research area
- Upload methods: arXiv URL (auto-extract metadata), other URL, or manual entry

### Step 3: Initial Citation Discovery
- System resolves each paper's Semantic Scholar ID
- Fetches 1-hop citations (papers citing it) and references (papers it cites)
- Filters by topic keywords if set
- Stores discovered papers, runs **Haiku structural skim using this topic's Structural Skim skill** on each; writes one row per (paper, topic) to `topic_paper_notes`
- User reviews the initial batch, adds human notes, and **adds selected papers to the Scout
  Seed List** to steer future scouting

### Step 4: Ongoing Scouting
- Scheduled weekly (or manually triggered): check for new citations of **seed papers only**
  (papers the user has explicitly added to the topic's Scout Seed List)
- New papers are filtered by topic keywords **and significant terms from problem statements**
  (words >4 chars extracted from each problem statement sentence)
- Haiku synthesis prompt includes: topic name, problem statements, keywords, and up to 5
  most-recently-annotated human notes from other papers in the topic
- Human notes on existing papers influence both filtering and synthesis context

## Scout Seed List

Each topic maintains a **Scout Seed List** — a curated subset of its associated papers whose
citation neighbourhoods are traversed during scouting.

### Rules
- **Default: not a seed.** Any paper added to a topic (whether manually uploaded or discovered
  via scouting) is NOT on the seed list unless the user explicitly adds it.
- **Stored as a flag** `is_scout_seed` on the `topic_papers` join row — no separate table needed.
- **Scouting uses seeds only.** Both "Scout Now" (manual) and the scheduled weekly job call
  Semantic Scholar only for papers with `is_scout_seed = 1` in the topic. Papers associated
  with a topic but not on the seed list are not used as traversal starting points.
- **"Discover Citations" on the paper detail page is unaffected** — that is a per-paper,
  user-triggered action independent of seed status.

### UI — Topic Detail Page
- The Papers panel in the topic detail page shows a seed toggle icon per paper (e.g. 🌱):
  - 🌱 lit/coloured = paper is a seed; clicking removes it from the seed list
  - 🌱 muted/outline = paper is not a seed; clicking adds it
- Toggle fires `POST /topics/{topic_id}/papers/{paper_id}/toggle-seed`, swaps the icon in-place via htmx
- No separate seed list section needed — the toggle is inline with each paper row

## Per-Paper Citation Enrichment

On any paper's detail page, a **"Discover Citations"** button allows the user to expand the citation graph from that paper:
- Fetches 1-hop citations + references via Semantic Scholar
- Filters out papers already in the DB
- New papers are stored and synthesized
- This is user-triggered, not automatic — the user decides which branches to explore

## Not Interesting Flag

Each paper–topic association can be marked **"not interesting"** by the user. This is a per-topic flag (stored on `topic_papers.not_interesting`) — a paper can be relevant to one topic and not to another.

### Behaviour
- Both `is_scout_seed` and `not_interesting` are **per-topic-paper** flags (stored on `topic_papers`) — the same paper can be a seed for one topic and not interesting to another.
- **Marking not interesting clears seeding**: when a paper is marked not interesting for a topic, `is_scout_seed` is simultaneously set to 0 for that topic. A not-interesting paper cannot be a scout seed.
- **Attempting to seed a not-interesting paper** is blocked with an error toast ("Mark as interesting first to seed this paper") — the icons refresh but no DB change is made.
- **Topic detail page paper list**: not-interesting papers appear muted/dimmed (reduced opacity 0.45) and sort to the bottom. Sort order: `read_next DESC, not_interesting ASC, published_date DESC, created_at DESC`.
- **Toggle icons**: 🌱 and 🚫 are co-rendered inside a shared `<span id="tp-icons-{topic_id}-{paper_id}">` container. Both toggles target and replace this container (`hx-target`, `hx-swap="outerHTML"`), so toggling either icon atomically refreshes both.
- **Paper detail page Topics section**: same shared container per topic row, same routes.
- Not-interesting papers are still shown (not hidden) so the user can review the full picture.

## Unprocessed Icon

Papers without a human note show a small **unprocessed indicator** (hollow circle `○`, muted orange) wherever papers are listed:

- **Topic detail page** — paper list in the right panel, inline after the paper title
- **Papers list page** — narrow column to the left of the title (same position as the Read Next icon column)

The icon disappears as soon as the user saves any human note for that paper (the template checks `has_human_note`, derived from `paper_notes.human_note IS NOT NULL AND != ''`). It is purely informational — no click action.

## Read Next Flag

Papers can be flagged as **"Read Next"** by the user — a simple bookmark/queue mechanism to track which papers to read soon.

- **Per-paper flag** (not per-topic): stored as `read_next` on the `papers` table
- **Togglable from two places**:
  - **Paper list page**: clickable icon per row, toggles instantly via htmx
  - **Paper detail page**: toggle button in the actions area
- **Filtering**: paper list can be filtered to show only "Read Next" papers
- **Visual indicator**: flagged papers are visually distinct in the list (e.g. bookmark icon)
- The flag is purely user-driven — not set by LLM synthesis or scouting

## Paper Detail Page Structure

Every paper detail page renders the following sections in order. Structural Skim and Deep Synthesis are **per (paper, topic)** — each section has a tab bar when the paper is linked to more than one topic.

```
Paper Info          — title, authors, venue, year, URL (paper-level, shared)

Structural Skim     — per-topic tab bar (one tab per linked topic, ordered by relevance_score DESC)
  [Topic A tab]       Pass 1: Main Claim, Data Source, Strategy Type, Headline Statistic
                      Pass 2: Signal Mechanism, Data Details, Sample, Universe,
                              Portfolio Construction, Key Tables, Key Metrics
                      Recommendation badge  ⭐ Deep Dive / 📖 Read / ⏭ Skip
                      Footer: last run · model · View skill ↗ · ⟳ if skill updated
  [Topic B tab]       (same structure, different topic's skill output)

Deep Synthesis      — per-topic tab bar (same tab order as Structural Skim)
  [Topic A tab]       Rich-text output from Sonnet/Opus using topic's Deep Synthesis skill
                      Empty state + "Generate Deep Synthesis" button until Phase 4 run
  [Topic B tab]       (same structure)

Abstract / Executive Summary   — paper-level

Human Note          — paper-level, shared across topics; Quill rich text editor

Topics              — linked topic badges with 🌱/🚫 per-topic toggles; relevance score

PDF                 — path/link display + inline edit + download/manage buttons
```

### Human Note

The **Human Note** section is written by the user, not the LLM. It serves two purposes:

1. **Per-paper annotation**: The user records what they think is important or unimportant. Displayed alongside LLM-generated sections on the paper detail page.

2. **Scouting steering input**: Human notes influence future scouting:
   - Positive notes ("the regime detection approach here is exactly what I need") signal that the citation neighborhood of this paper is worth exploring
   - Negative notes ("too simplistic, not useful") signal to de-prioritize that branch
   - Notes are fed into the Haiku prompt when synthesizing newly discovered papers
   - Notes are surfaced in the topic steering UI as context for keyword/problem statement adjustments

### Human Note in the webapp
- **Paper detail page**: All structured note sections displayed. Human Note uses a Quill rich text editor (bold, colour, lists, image/screenshot paste, hyperlinks).
- Each paper detail page has a **Copy Link** button that copies an HTML anchor to the clipboard. Pasting this into another paper's Human Note creates a clickable cross-paper reference link.
- **Topic steering page**: Aggregated human notes for a topic shown as context for steering decisions.

## Manual Paper Upload

Users can manually add papers to a topic at any time, not just during establishment.

### How it works
- **Topic detail page** and **Papers list page** have an "Add Paper" button that opens an upload form
- The user provides either:
  - A **URL** (arXiv or DOI auto-extracts metadata; any other link stored as-is)
  - A **PDF** — two modes:
    - **Upload new**: upload a file and choose which subfolder under `Papers, Presentation, Reports and Slides` to save it in
    - **Link existing**: select a subfolder then a filename from PDFs already present in `Papers, Presentation, Reports and Slides`; poneglyph records the path without copying the file
  - **Manual entry** — fill in title, authors, abstract, URL directly (no PDF)
- Paper created with `source = 'manual'`, linked to selected topics
- Uploaded papers serve as starting points for citation discovery

### Metadata auto-population priority
When a user uploads a paper via URL or PDF, the system auto-populates metadata fields (title, authors, abstract, venue, date) using a fallback chain:

1. **Source API metadata** (free, instant, authoritative):
   - **arXiv URLs**: detected by pattern, metadata fetched via arXiv Atom API
   - **Other recognized URLs**: resolved via Semantic Scholar API if possible
2. **LLM extraction from PDF** (Haiku, ~$0.001/paper):
   - If no source API metadata is available and a PDF is uploaded, Haiku reads the first few pages and extracts structured metadata
3. **Manual fill-in** (user fallback):
   - If both above fail (no recognized URL, no PDF, or LLM unavailable), the user fills in fields manually

User-provided values always take precedence — auto-populated fields are only used for blank fields. A toast indicates the metadata source: "Metadata from arXiv" / "Metadata extracted from PDF" / "Please fill in metadata manually".

### Multi-topic association
A single paper can be associated with multiple topics via `topic_papers`. Each association has its own relevance score, recommendation, **structural skim**, and **deep synthesis** (stored in `topic_paper_notes`, one row per (paper, topic) pair). Human notes are per-paper (shared across topics). The paper detail page surfaces the per-topic outputs through tabbed sections.

## Synthesis Workflow

Synthesis is **per-topic and per-paper** (one set of outputs per (paper, topic) pair). All LLM reading is driven by the topic's uploaded skill prompts. Three kinds of LLM output exist:

1. **Structural Skim** (Phase 2, Haiku): runs the topic's **Structural Skim skill** against the paper to produce Pass 1 (Orientation) + Pass 2 (Structural skim). Stored per (paper, topic) in `topic_paper_notes`. Includes a `skim_recommendation` of `read | skip | deep_dive`.
   - **Auto-fires for every paper entering the system**, regardless of how it got there:
     - Scheduled scouting (weekly Task Scheduler run)
     - Manual "Scout Now" on a topic
     - Per-paper "Discover Citations" enrichment
     - Manual upload (URL, PDF upload, PDF link, manual entry) — one skim run per topic the paper is linked to at upload
   - **"Generate Structural Skim"** button on the paper detail page (per topic tab) lets the user re-run or first-time generate the skim manually; always available regardless of prior state.
2. **Deep Synthesis** (Phase 4, Sonnet/Opus): runs the topic's **Deep Synthesis skill** against the full paper text (PDF if available) and produces rich-text output stored in the same (paper, topic) row.
   - **Never auto-fires.** Always user-triggered from the **"Generate Deep Synthesis"** button on the paper detail page (per topic tab).
   - The workflow is: user reads the structural skim in a topic tab, decides it's worth deeper analysis (often because `skim_recommendation == 'deep_dive'`), clicks the button, and receives the deep synthesis in the same tab.
3. **Cross-paper synthesis** (Phase 4, Sonnet/Opus, user-triggered): Synthesizes across all papers in a topic, framed around problem statements. Identifies themes, gaps, conflicting results, and suggests research directions. Human notes feed into the prompt. Stored in `cross_syntheses`. Triggered by a **"Synthesize Topic"** button on the topic detail page — never runs automatically.

### Button placement summary
Both synthesis passes have a dedicated button on the paper detail page, inside the active per-topic tab:
- **Generate Structural Skim** — Haiku, Phase 2. Visible on every tab; always available for regeneration.
- **Generate Deep Synthesis** — Sonnet/Opus, Phase 4. Visible on every tab; the only way to trigger deep synthesis (no automatic path).

### Where synthesis lives in the UI
- **Topic detail page**: Latest cross-paper synthesis as collapsible section; "Skills" panel with Structural Skim skill and Deep Synthesis skill (upload/edit).
- **Paper detail page**: Two per-topic tabbed sections — **Structural Skim** and **Deep Synthesis**. Each tab corresponds to a topic the paper is linked to. Each tab shows the output generated with that topic's skill, plus a "Regenerate" button and a "View skill" link.
- **Dashboard**: Topics and Papers cards only — no standalone Synthesis section

### Per-topic tabbed UI on the paper detail page
- A paper linked to 1 topic renders as a single section (no tab bar).
- A paper linked to N>1 topics renders a tab bar above each of the Structural Skim and Deep Synthesis sections; one tab per linked topic (ordered by `topic_papers.relevance_score DESC` from Phase 3, then by `topics.name`). Relevance scores are computed independently of skills and LLM outputs, so tab order stays stable even when a skill prompt is edited.
- The tab state is preserved in the URL (`?topic={topic_id}`) so tabs are deep-linkable and shareable.
- Each tab shows:
  - The rendered skim / deep synthesis content for that (paper, topic) pair (or an empty placeholder + Generate button if not yet run)
  - `Regenerate with {topic name}'s skill` button
  - Small footer: `Last run: {timestamp}` · `model: {model_used}` · `View skill ↗` (modal shows the skill prompt used)
  - A "⟳ Skill updated since last run" badge if `topic_paper_notes.skim_skill_hash` / `deep_skill_hash` no longer matches the topic's current skill hash, prompting regeneration
- Linking/unlinking a topic adds/removes a tab. Unlinking cascade-deletes the (paper, topic) row.

## User Q&A Over Papers

Users can ask natural language questions about their paper collection.

- Questions answered via vector similarity search over paper embeddings + FTS5
- Results optionally refined by an LLM call that reads top-N matching notes and answers with citations
- Available via an "Ask about papers" input on the search page

## Topic Steering

Users steer scouting direction per topic through two channels:

### Direct steering (topic detail page)
- **Keywords**: add/remove keywords that filter the citation graph results
- **Problem statements**: free-text descriptions of specific problems — drives relevance scoring and LLM prompts
- **Steering log**: all changes tracked so users can see how their direction evolved

### Indirect steering (via Human Notes on papers)
- Human notes on papers feed back into topic steering
- Topic steering page shows aggregated human notes for context
- A lightweight LLM call can parse recent notes and suggest keyword/problem statement adjustments
- Suggestions appear for user approval (not auto-applied)
- Feedback loop: read papers → annotate → steer → scout better papers
