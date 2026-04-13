# Phase 1b: Frontend Polish, Paper Pages & Desktop Launcher

## Deliverables
- Refined base template and navigation (Papers active, Search greyed out. No Synthesis link — synthesis is per-topic.)
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
- [x] Add nav links for Papers (active), Search (disabled placeholder). No Synthesis link — synthesis is per-topic, accessed from topic detail page.
- [x] Dark mode is the **default** (`data-theme="dark"`). Toggle persisted in localStorage.
- [x] Loading indicator for htmx requests (htmx `hx-indicator`)
- [x] Create `scripts/launch_webapp.pyw` — starts uvicorn subprocess (no console), opens browser to `http://127.0.0.1:8000`
- [x] Add desktop shortcut creation to `scripts/setup_scheduler.py` — creates a `.lnk` on the user's Desktop
- [x] Idempotent launcher: if server already running, just open browser; if port stuck, kill stale process first

### Paper pages (moved from Phase 2)
- [x] Paper list page (`/papers`) — compact table, one row per paper: Title | Authors (citation format). Filtered by topic dropdown. Relevance sorting comes in Phase 3. No source column — source labels are not shown in the UI.
- [x] Paper detail page (`/papers/{id}`) — displays all structured note sections:
  - Paper Info (title, authors, venue, year, link)
  - Key Insights (initially empty, populated in Phase 4)
  - Applications for Trading & Investments (initially empty)
  - Abstract/Executive Summary
  - Human Note — inline editable text area (htmx `PUT /papers/{id}/human-note`)
- [x] All Paper Info fields are inline-editable (title, authors, venue, date, URL, abstract) — not just the human note
- [x] Paper detail shows associated topics with links
- [x] Enable the "Papers" nav link (no longer greyed out)

### Manual paper upload
- [x] "Add Paper" button available on **both** the topic detail page and the Papers list page
- [x] Upload form includes a **Topics** field with multi-select checkboxes for all topics
  - When opened from a topic detail page, that topic's checkbox is pre-selected
  - When opened from the Papers list page, no topics are pre-selected
- [x] Three upload modes:
  - **URL**: paste a link. arXiv URLs are detected and metadata auto-extracted via arXiv API (title, authors, abstract, pdf_url). Other URLs fall back to manual entry.
  - **PDF upload**: upload a new PDF file (choose subfolder to save in) — LLM (Haiku) extracts title, authors, abstract, venue, date; falls back to manual entry if extraction fails
  - **PDF link**: select an existing PDF already in `Papers, Presentation, Reports and Slides` by choosing subfolder then filename — poneglyph links it without copying
  - **Manual entry**: fill in title, authors, abstract, URL directly (no PDF)
- [x] Paper created with `source = 'manual'`, `source_id` generated as UUID to satisfy UNIQUE(source, source_id) constraint
- [x] Paper linked to all selected topics via `topic_papers`
- [x] "Add to topic" dropdown on paper detail page — link existing paper to additional topics
- [x] Dedup check: if a paper with same URL or title already exists, link to existing rather than creating duplicate
- [x] arXiv URL auto-extraction uses last revised date (`atom:updated`) rather than first submitted (`atom:published`)
- [x] Delete paper button on paper detail page with confirmation dialog

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
- [x] Edit Topic form covers: name, description, keywords, priority keywords, problem statements, sources (arXiv/SSRN/Kaggle checkboxes), PDF Policy
- [x] PDF Policy display name: **"Source link only"** (was "Link only")

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
- [x] Manage PDF dialog supports two modes: **Upload new file** (save to chosen subfolder, optionally extract metadata) or **Link existing file** (pick folder + filename from files already on disk, no copy made)
- [x] "Link existing file" File field is a searchable `<datalist>` input — user can type to filter filenames; populated by HTMX when folder is selected
- [x] PDF location removed from Paper Info section (top) — already shown in the PDF row at the bottom of the detail page
- [x] Paper URL shown as a small muted link directly under the title (not a "Link:" label row); blank if no URL. "View Link" button removed from action bar. Priority: scouted papers use source URL (arXiv/DOI set at upload); manual papers use user-entered DOI/URL; otherwise blank.
- [x] Human Note uses Quill rich text editor: bold, italic, underline, strike, text colour, background colour, ordered/unordered lists, image embed, link; screenshot paste encoded as data URL inline
- [x] Right-click context menu on selected text in Quill editor — shows "Add link" option; opens a URL input dialog; inserts link via `quill.format('link', url)`; menu repositions to stay within viewport
- [x] Side panel (fixed right drawer, 45% width, CSS slide-in transition) — clicking any link in the Human Note view mode opens the panel with an iframe of the URL; header shows URL, "Open in tab ↗" fallback, and close button; panel, context menu, and dialog are DOM singletons that survive htmx re-renders of the note section

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
The paper detail page is the primary reading interface. All structured note sections are rendered in order (Paper Info → Key Insights → Trading Applications → Abstract → Human Note). Sections not yet populated (before bulk synthesis in Phase 4) show "Pending synthesis" placeholder. The Human Note is always editable regardless of synthesis status.

### Manual Upload
Papers uploaded manually use `source = 'manual'` and a generated UUID as `source_id` to satisfy the UNIQUE constraint on `(source, source_id)`. URL-based uploads attempt metadata extraction: arXiv URLs are parsed for the paper ID and fetched via the arXiv API; other URLs fall back to manual entry. PDF uploads are stored in `data/pdfs/` via `pdf_manager.py`.

## Dependencies
- Phase 1 (database, app skeleton, topic CRUD)
