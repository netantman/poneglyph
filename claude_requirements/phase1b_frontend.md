# Phase 1b: Frontend Polish, Paper Pages & Desktop Launcher

## Deliverables
- Refined base template and navigation (Papers active. No Synthesis link — synthesis is per-topic. Search accessible via dashboard block.)
- Toast/notification component for htmx feedback (e.g. "Topic saved", "Paper uploaded")
- Dark mode toggle (Pico CSS supports `data-theme="dark"`)
- Desktop shortcut launcher (`scripts/launch_webapp.pyw` + `.lnk` shortcut creation)
- Loading indicator for htmx requests
- Paper list and detail pages (moved from Phase 2)
- Manual paper upload (URL, PDF, or manual entry)
- Paper note page displaying all structured note sections

## TODO

### Frontend polish
- [x] Add toast/notification component for user feedback on htmx actions
- [x] Add nav link for Papers. No Synthesis link — synthesis is per-topic, accessed from topic detail page. Search accessible via dashboard block (not a nav link).
- [x] Dark mode is the **default** (`data-theme="dark"`). Toggle persisted in localStorage.
- [x] Loading indicator for htmx requests (htmx `hx-indicator`)
- [x] Create `scripts/launch_webapp.pyw` — starts uvicorn subprocess (no console), opens browser to `http://127.0.0.1:8000`
- [x] Add desktop shortcut creation to `scripts/setup_scheduler.py` — creates a `.lnk` on the user's Desktop
- [x] Idempotent launcher: if server already running, just open browser; if port stuck, kill stale process first

### Paper pages (moved from Phase 2)
- [x] Paper list page (`/papers`) — compact table, one row per paper: Title | Authors (citation format). Filtered by topic dropdown. Relevance sorting comes in Phase 3. No source column — source labels are not shown in the UI.
- [x] Paper detail page (`/papers/{id}`) — displays all structured note sections:
  - Paper Info (title, authors, venue, year, link)
  - **Structural Skim** — rich-format display of Pass 1 + Pass 2 outputs, **rendered per-topic**:
    - If the paper is linked to exactly 1 topic: single section, no tab bar.
    - If linked to ≥2 topics: **tab bar** above the section with one tab per linked topic (ordered by `topic_papers.relevance_score DESC` from Phase 3, then `topics.name`). Active tab state preserved in URL (`?topic={topic_id}`). Tab swap via htmx `GET /papers/{id}/structural-skim?topic_id={tid}` returning the partial.
    - Each tab shows: Main Claim, Data Source & Sample Period, Strategy Type, Headline Statistic, Signal Mechanism, Data Details, Sample, Universe, Portfolio Construction, Key Tables, Key Metrics; recommendation badge (⭐ Deep Dive / 📖 Read / ⏭ Skip); footer with `Last run / model / View skill ↗` (modal showing the skill used).
    - "⟳ Skill updated since last run" badge appears on the tab when `topic_paper_notes.skim_skill_hash` ≠ current hash of `topics.skim_skill_md`.
    - Empty state when no skim exists for the active tab: "No structural skim for {topic name} yet." + Generate button.
  - **"Generate Structural Skim with {topic} skill"** button in the active tab's footer — `POST /papers/{id}/structural-skim` with `topic_id={active}`; runs Haiku Pass 1 + Pass 2 against that topic's Structural Skim skill; htmx swaps the skim tab panel on completion; disabled with spinner while running. Disabled with tooltip "Upload a Structural Skim skill for this topic first" when the topic has no skill and the default is opted out.
  - **Deep Synthesis** — same per-topic tabbed layout, populated by Phase 4; shows an empty state with a "Generate Deep Synthesis" button until Phase 4 is implemented.
  - Abstract/Executive Summary
  - Human Note — **click-to-edit modal**: clicking anywhere on the Human Note section (view area) opens a `<div>`-based overlay (not native `<dialog>`, which Pico CSS overrides) with the Quill rich text editor at ~60 vh. The modal has a **Save** button (`PUT /papers/{id}/human-note`) and a **Cancel** button that discards changes; clicking the backdrop or ✕ also discards. Screenshot paste (data URL inline) and all Quill formatting are preserved inside the dialog editor. All singleton JS (Quill init, overlays, side panel, link dialog, ctx menu) lives in `detail.html` — not in the htmx-swappable partial — so it survives re-renders.
- [x] All Paper Info fields are inline-editable (title, authors, venue, date, URL, abstract) — not just the human note
- [x] Paper detail shows associated topics with links — each topic badge links to the topic detail page (`/topics/{id}`), not the filtered papers list
- [x] Enable the "Papers" nav link (no longer greyed out)

### Manual paper upload
- [x] "Add Paper" button available on **both** the topic detail page and the Papers list page
- [x] Upload form includes a **Topics** field with multi-select checkboxes for all topics
  - When opened from a topic detail page, that topic's checkbox is pre-selected
  - When opened from the Papers list page, no topics are pre-selected
- [x] Three upload modes:
  - **URL**: paste a link. arXiv URLs are detected and metadata auto-extracted via arXiv API (title, authors, abstract, pdf_url). Other URLs fall back to manual entry.
  - **PDF upload**: upload a new PDF file (choose subfolder to save in) — LLM (Haiku) extracts title, authors, abstract, venue, date; falls back to manual entry if extraction fails
  - **PDF link**: select an existing PDF already in `Papers, Presentation, Reports and Slides` by typing to search across all PDFs recursively (shown as relative paths) — poneglyph links it without copying
  - **Manual entry**: fill in title, authors, abstract, URL directly (no PDF)
- [x] Paper created with `source = 'manual'`, `source_id` generated as UUID to satisfy UNIQUE(source, source_id) constraint
- [x] Paper linked to all selected topics via `topic_papers`
- [x] "Add to topic" dropdown on paper detail page — link existing paper to additional topics
- [x] Dedup check: if a paper with same URL or title already exists, link to existing rather than creating duplicate
- [x] arXiv URL auto-extraction uses last revised date (`atom:updated`) rather than first submitted (`atom:published`)
- [x] Delete paper button on paper detail page with confirmation dialog

### Topics list page layout ✅
- [x] `/topics` displays topics as a **5-column table** (replacing the card-per-topic layout):
  - **Topic** — name as link to `/topics/{id}`, Active/Paused badge, Edit / Pause / Delete action buttons
  - **Keywords** — count badge (`<details>` popover) listing priority + regular keywords
  - **Problem Statements** — count badge (`<details>` popover) listing all statements as a numbered list
  - **Skim Skill** — "✓ Set" (green) or "✗ None" (muted); clicking opens inline skill editor via htmx in the cell
  - **Deep Synthesis** — same as Skim Skill
- Create form targets `#topic-tbody afterbegin`; edit/delete target `#topic-{id} outerHTML` — all htmx wiring unchanged
- `<details class="kw-popover">` with absolute-positioned `.kw-drop` panel; CSS lives in `list.html`

### Read Next flag
- [x] "Read Next" toggle on paper list page — clickable 🔖/📄 icon as a column per row, toggles via htmx without page reload
- [x] "Read Next" toggle on paper detail page — next to the paper title in Paper Info section
- [x] Paper list can be filtered to show only "Read Next" papers (checkbox filter)
- [x] Visual indicator: 🔖 (bookmark) for flagged, 📄 (page) for unflagged

### Unprocessed flag
- [x] `papers.unprocessed` column — `INTEGER NOT NULL DEFAULT 1`; new papers start unprocessed
- [x] Toggle button on paper detail page (actions bar): shows "○ Unprocessed" (amber) when unprocessed,
  "✓ Processed" (muted outline) when processed — htmx `POST /papers/{id}/unprocessed`, swaps button in-place
- [x] Amber `○` indicator in paper list page (narrow column between Read Next and Title) for unprocessed papers
- [x] Amber `○` indicator inline after paper title in topic detail page paper panel
- [x] Indicator disappears when paper is marked processed; reappears if toggled back

### Not Interesting Flag
- [x] `topic_papers.not_interesting` column — `INTEGER NOT NULL DEFAULT 0`; both `not_interesting` and `is_scout_seed` are per-topic-paper (on `topic_papers`), not per-paper
- [x] Marking a paper not interesting for a topic **automatically clears its scout seed flag** for that topic (`is_scout_seed` set to 0 in same UPDATE)
- [x] Attempting to seed a not-interesting paper returns an error toast and no DB change
- [x] 🌱 and 🚫 co-rendered in a shared `<span id="tp-icons-{topic_id}-{paper_id}">` container; both toggles target and replace this container so both icons refresh atomically
- [x] Not-interesting papers rendered with reduced opacity (0.45) in the topic detail paper list
- [x] Not-interesting papers sort to the bottom: `ORDER BY p.read_next DESC, tp.not_interesting ASC, p.published_date DESC, p.created_at DESC`
- [x] 🚫 toggle on paper detail page Topics section — per topic row, same shared container pattern
- [x] Sort order updated to include `p.published_date DESC` — latest-published papers float up within each group

### Scout Seed List (topic detail page)
- [x] `topic_papers.is_scout_seed` column — `INTEGER NOT NULL DEFAULT 0`; papers are not seeds by default
- [x] Seed toggle icon per paper in the topic detail Papers panel, inline after the unprocessed indicator:
  - 🌱 in active colour = seed; 🌱 muted = not a seed
  - `POST /topics/{topic_id}/papers/{paper_id}/toggle-seed` — toggles `is_scout_seed`, swaps icon in-place via htmx `hx-target="this" hx-swap="outerHTML"`
- [x] Toast on toggle: "Added to scout seeds" / "Removed from scout seeds"
- [x] Scout Now button confirmation text updated to reflect seed count: "Scout citations for {n} seed paper(s)…"
- [x] "View all papers →" replaced with inline expand: htmx `GET /topics/{id}/papers-list` swaps
  `#topic-papers-list` innerHTML with all papers (no LIMIT) — same row format with 🌱 toggles
  - Initial render shows first 20; "Show all papers →" link appears only when list may be truncated (≥20 shown)
  - Partial template: `templates/topics/partials/papers_list.html`
- [x] Topic detail page Papers panel sorted by `read_next DESC, created_at DESC` — Read Next papers float to top; applies to both initial 20 and full expanded list
- [x] Search bar in topic detail page Papers panel — htmx `GET /topics/{id}/papers-list?q=` on input (300ms delay), filters by title/author, swaps `#topic-papers-list` innerHTML
- [x] 🔖/📄 Read Next icon shown per paper in the topic detail Papers panel — same htmx toggle as the papers list page, swaps in-place
- [x] 🌱 seed toggle on paper detail page Topics section — inline next to each topic badge
  - `_get_paper_topics` extended to include `tp.is_scout_seed`
  - Same `POST /topics/{topic_id}/papers/{paper_id}/toggle-seed` route, swaps icon in-place
  - Tooltip identifies the topic: "Add to scout seeds for {topic name}" / "Remove from…"

### Topic management
- [x] Edit Topic form accessible inline from topic detail page (HTMX, scrolls to top) — handles both list-page inline swap and detail-page redirect on save
- [x] Edit Topic form covers: name, description, keywords, priority keywords, problem statements
- [x] Priority Keywords collapsed into Keywords — single Keywords field on both form and detail page; `priority_keywords` DB column always saved as `[]`; existing priority keywords merged into keywords display
- [x] Relevance score shown per paper in the topic detail paper list — displayed inline after authors as a muted 2 d.p. number; hidden when score is 0 or null
- [x] PDF Policy removed — PDFs are never auto-downloaded; acquisition happens on-demand at deep synthesis time
- [x] **Topic Skills panel** on Edit Topic form — two sections:
  - **Structural Skim skill** (Haiku, Phase 2): file-upload (`.md`) + inline `<textarea>` with a monospace font; stored in `topics.skim_skill_md`; **required** before scouting or structural skim can run for this topic — no built-in default
  - **Deep Synthesis skill** (Sonnet/Opus, Phase 4): same controls; stored in `topics.deep_synthesis_skill_md`; **required** before deep synthesis can run for this topic — no built-in default
  - Validation: reject blank/whitespace on save
- [x] **Topic Detail page header** shows two small badges:
  - `Skim skill: ✓ Custom` / `✗ None`
  - `Deep skill: ✓ Custom` / `✗ None`
  - Clicking either badge scrolls to the Topic Skills panel (or opens the edit form if closed)
- [x] **Scout Now** button on topic detail page is disabled (with tooltip "Upload a Structural Skim skill first") when `skim_skill_md` is NULL.

### Cleanup
- [x] Remove source column from paper list table (Title | Authors only, no Source column)
- [x] Remove source badge from paper detail page
- [x] Remove source badge CSS (`.badge-manual`, `.badge-arxiv`, `.badge-ssrn`, `.badge-kaggle`, `.badge-source`) from `base.html`
- [x] Rename "Open Original" button to "View Link" on paper detail page
- [x] "View PDF" button greyed out (disabled) when no local PDF is saved for the paper
- [x] Desktop launcher always kills existing server and restarts (ensures latest code)
- [x] All PDFs stored under `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\` in user-chosen subfolders
- [x] On PDF upload, user selects a subfolder from a dropdown (populated from actual subfolders on disk)
- [x] Future scouted papers saved to `poneglygh_processing` subfolder automatically
- [x] "Update PDF" opens a dialog to optionally change subfolder and/or upload a new PDF; moving the file if subfolder changes
- [x] Upload form error responses use `HX-Reswap: none` so the form stays visible on failure; error toast describes what went wrong specifically (PDF extraction failed vs. no title provided vs. arXiv fetch failed)
- [x] PDF naming: `Public-Academia` and the scouting subfolder (`poneglyph_processing`) use `LastName1, LastName2, ... and LastNameN, (year), Title.pdf` (last names only, alphabetical, last two joined with "and"); all other subfolders use `Title.pdf`
- [x] "View PDF" button saves a copy to `~/Desktop/poneglyph_working_papers/` with a toast (does not open in browser)
- [x] Paper detail page shows the full PDF path if linked — inline editable via `PUT /papers/{id}/pdf/path`; updates DB only (does not move the file)
- [x] Download PDF and Manage PDF (move) both check file existence before acting; toast error includes the missing path if not found
- [x] Paper detail page has a **Copy Link** button that copies an HTML anchor (`<a href="/papers/{id}">Title</a>`) to the clipboard — can be pasted directly into another paper's Quill Human Note to create a clickable cross-paper link
- [x] Manage PDF dialog supports two modes: **Upload new file** (save to chosen subfolder, optionally extract metadata) or **Link existing file** (pick any PDF already on disk, no copy made)
- [x] "Link existing file" uses a **single searchable `<datalist>` input** showing all PDFs recursively as relative paths (e.g. `Research/Quant/paper.pdf`); populated once on page load via `GET /papers/pdf/all-files`; no folder dropdown — works at any nesting depth
- [x] PDF location removed from Paper Info section (top) — already shown in the PDF row at the bottom of the detail page
- [x] Paper URL shown as a small muted link directly under the title (not a "Link:" label row); blank if no URL. "View Link" button removed from action bar. Priority: scouted papers use source URL (arXiv/DOI set at upload); manual papers use user-entered DOI/URL; otherwise blank.
- [x] Human Note uses Quill rich text editor: bold, italic, underline, strike, text colour, background colour, ordered/unordered lists, image embed, link; screenshot paste encoded as data URL inline
- [x] Right-click context menu on selected text in Quill editor — shows "Add link" option; opens a URL input dialog; inserts link via `quill.format('link', url)`; menu repositions to stay within viewport
- [x] Side panel (fixed right drawer, 45% width, CSS slide-in transition) — clicking any link in the Human Note view mode opens the panel with an iframe of the URL; header shows URL, "Open in tab ↗" fallback, and close button; panel, context menu, and dialog are DOM singletons that survive htmx re-renders of the note section
- [x] Human Note view area is clickable (cursor: pointer, subtle hover highlight) — clicking anywhere on it opens a `<div>` overlay modal with the full Quill editor (~60 vh); modal has **Save** and **Cancel** buttons; Cancel/close discards unsaved changes; Save calls `PUT /papers/{id}/human-note` and swaps the view content

## Details

### Desktop Launcher
`launch_webapp.pyw` is an idempotent launcher with the following logic:
1. **If server is already running** (port 8000 accepts connections): just open the browser, don't start a second server
2. **If port is stuck** (stale process from a previous session): detect via `netstat`, kill the process via `taskkill /F /PID`, wait for port to clear
3. **If port is free**: start uvicorn as a subprocess with `CREATE_NO_WINDOW` flag (no console window), poll until server accepts TCP connections (up to 30s), then open browser
4. Uses `python.exe` (not `pythonw.exe`) for reliable module resolution — the `CREATE_NO_WINDOW` flag handles console suppression

`setup_scheduler.py` creates a `.lnk` desktop shortcut. Handles OneDrive Desktop path. Uses custom icon from `static/icon.ico`.

### Toast Component
A simple htmx-compatible toast: server returns an `HX-Trigger: showToast` header with a message, and a small JS listener displays it briefly. No library needed.

### Paper Note Page
The paper detail page is the primary reading interface. Sections render in order: **Paper Info → Structural Skim → Deep Synthesis → Abstract → Human Note → Topics → PDF**.

- **Structural Skim** and **Deep Synthesis** are **per (paper, topic) pair**, pulled from `topic_paper_notes`. For multi-topic papers, each section renders a tab bar (one tab per linked topic, ordered by `topic_papers.relevance_score DESC` from Phase 3). The active tab is reflected in the URL (`?topic={topic_id}`) so tabs are deep-linkable.
- Each tab's content is the output of that topic's skill on this paper (Structural Skim skill for Phase 2, Deep Synthesis skill for Phase 4). A "View skill" link in the tab footer opens a modal showing the exact prompt used.
- When a topic's skill changes, the corresponding tab shows a "⟳ Skill updated since last run" badge until regenerated.
- The **Human Note** is paper-level (shared across topics) and always editable.

### Manual Upload
Papers uploaded manually use `source = 'manual'` and a generated UUID as `source_id` to satisfy the UNIQUE constraint on `(source, source_id)`. URL-based uploads attempt metadata extraction: arXiv URLs are parsed for the paper ID and fetched via the arXiv API; other URLs fall back to manual entry. PDF uploads are stored in `data/pdfs/` via `pdf_manager.py`.

## Dependencies
- Phase 1 (database, app skeleton, topic CRUD)
