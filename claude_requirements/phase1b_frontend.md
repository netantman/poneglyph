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
  - **PDF upload**: upload a PDF file. Extract title/abstract via pypdf, other fields manual
  - **Manual entry**: fill in title, authors, abstract, URL directly
- [x] Paper created with `source = 'manual'`, `source_id` generated as UUID to satisfy UNIQUE(source, source_id) constraint
- [x] Paper linked to all selected topics via `topic_papers`
- [x] "Add to topic" dropdown on paper detail page — link existing paper to additional topics
- [x] Dedup check: if a paper with same URL or title already exists, link to existing rather than creating duplicate
- [x] arXiv URL auto-extraction uses last revised date (`atom:updated`) rather than first submitted (`atom:published`)
- [x] Delete paper button on paper detail page with confirmation dialog

### Cleanup
- [x] Remove source column from paper list table (Title | Authors only, no Source column)
- [x] Remove source badge from paper detail page
- [x] Remove source badge CSS (`.badge-manual`, `.badge-arxiv`, `.badge-ssrn`, `.badge-kaggle`, `.badge-source`) from `base.html`

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
