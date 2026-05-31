# Phase 9: Literature Rename + Books

## Goal

Two related changes shipped together:

1. **Rename "Papers" → "Literature"** everywhere in the UI — nav, page titles, breadcrumbs,
   buttons, headings. The underlying routes (`/papers/…`) and DB table (`papers`) are
   unchanged; this is a display-layer rename only.

2. **Add "Book" as a content type** — a lightweight entry for ebooks/reference books. Books
   share the `papers` table (`content_type = 'book'`) but have a distinct upload flow and
   a simplified detail page (no structural skim, no deep synthesis, no citation scouting).

---

## 1. UI rename: Papers → Literature

### Affected locations

| Location | Old | New |
|---|---|---|
| Nav link | Papers | Literature |
| `/papers` page `<h1>` | Papers | Literature |
| Breadcrumb on paper detail | Papers | Literature |
| Breadcrumb on book detail | Papers | Literature |
| "Back to Papers" button on paper detail | Back to Papers | Back to Literature |
| "Back to Papers" button on book detail | — | Back to Literature |
| "Add Paper" button (topic detail + literature list) | Add Paper | Add Literature |
| Upload form `<header>` | Add Paper | Add Literature |
| Toast on upload success | Paper '…' uploaded | Literature '…' added |
| Dashboard block label | Papers | Literature |
| `<title>` tags | … - Poneglyph | … - Poneglyph (unchanged) |

### What does NOT change

- Route paths (`/papers`, `/papers/{id}`, etc.) — no redirects needed.
- DB table name, column names, model dicts.
- Any backend logging strings — cosmetic only.

---

## 2. Book content type

### 2.1 Data model

`papers.content_type` already exists (`TEXT NOT NULL DEFAULT 'academic'`). Books use
`content_type = 'book'`.

New column added in migration v6:
```sql
ALTER TABLE papers ADD COLUMN onenote_url TEXT;
```
Stores an optional OneNote page URL. Applies to all content types but surfaced primarily
for books.

New config entry in `config.py`:

```python
ebook_library_dir: str = r"C:\Users\zhong\OneDrive\Ebook Library\References"
```

Overridable via `.env` as `EBOOK_LIBRARY_DIR`.

New helpers in `services/pdf_manager.py`:

```python
def get_ebook_library_dir() -> Path: ...
def save_ebook_pdf(content: bytes, filename: str) -> Path:
    """Save PDF bytes directly into ebook_library_dir. Returns full path."""
```

No subfolder inside the ebook library — all books saved flat at the root of
`ebook_library_dir` as `{sanitized_title}.pdf`.

### 2.2 Upload flow

The "Add Literature" form gains a **Paper / Book** toggle (radio, default Paper) at the top.
Switching the toggle re-renders the form fields JS-side (no server round-trip needed —
just show/hide sections with JS).

#### Paper mode (unchanged behaviour)
All existing fields, PDF modes, subfolder selection, run-synthesis toggle — exactly as today.

#### Book mode

**Shown fields:**
- Book name (maps to `title`, required)
- Authors (comma-separated, optional)
- OneNote Link (`onenote_url`, optional) — URL to the OneNote page for this book

**Hidden fields (not shown, not submitted):**
- URL
- Abstract / Executive Summary
- Published Venue
- Published Date
- Run structural skim toggle (skim never runs for books — hidden entirely)

**PDF section:**
- Upload new file only — no "Link existing file", no "No PDF" option.
- PDF is **optional** for books — can be added later via Manage PDF.
- Save location is fixed: `ebook_library_dir` — no subfolder dropdown shown.
- Filename: `{sanitized_title}.pdf` (same as "None, existing location" convention).

**Route behaviour for `content_type='book'`:**
- Skip all LLM metadata extraction (no Haiku call).
- Skip arXiv / CrossRef / DOI resolution.
- Skip relevance scoring (no topic embeddings needed at upload time; scoring still
  runs when the book is linked to a topic via the detail page).
- Skip auto-skim entirely (books have no skim skill path).
- If PDF uploaded: save to `ebook_library_dir`, record `pdf_local_path`.
- If `onenote_url` provided: save to `papers.onenote_url`.
- Link to any pre-selected topics as usual (`topic_papers` rows, `is_scout_seed = 0`).
- `source = 'manual'`, `source_id = uuid`.
- Toast: "Book '{title}' added"

**Validation (server-side, same error-response pattern as paper upload):**
1. `title` blank → inline error "Book name is required."

### 2.3 Literature list page (`/papers`)

- Content-type chip shown for books: `book` chip (same style as `article` chip, different
  label). Academic papers show no chip (unchanged).
- Filtering, search, sort order, Read Next toggle — all work the same for books.
- "Add Literature" button opens the shared upload form (Paper/Book toggle visible).

### 2.4 Book detail page

The detail page is already per-topic (`/papers/{id}/topics/{tid}`) for papers. For books
the same URL structure is used, but the template renders a simplified layout.

**Sections shown for books:**

| Section | Shown? | Notes |
|---|---|---|
| Topic tabs (top) | ✓ | Same as papers |
| Book Info | ✓ | Title, authors, OneNote link (if set) — no venue/date/URL/abstract rows |
| Structural Skim | ✗ | Hidden entirely |
| Deep Synthesis | ✗ | Hidden entirely |
| Paper Q&A | ✗ | Hidden entirely (Phase 4c — books are reference material, not analytical targets) |
| Human Note | ✓ | Per-topic, same Quill editor modal |
| Topics | ✓ | Add/remove associations, same as papers |
| PDF path | ✓ | Shows path to ebook; inline edit; View PDF / Download PDF / Manage PDF buttons |

**Action bar buttons for books:**

| Button | Shown? | Notes |
|---|---|---|
| Back to Literature | ✓ | Renamed from "Back to Papers" |
| Copy Link | ✓ | Same behaviour |
| ○ Unprocessed / ✓ Processed | ✓ | Same toggle |
| Read Next (🔖/📄) | ✓ | Same toggle |
| Download PDF | ✓ | Same behaviour |
| Manage PDF | ✓ | Same behaviour (move/re-link) |
| Delete | ✓ | Same cascade confirmation |
| Discover Citations | ✗ | Hidden for books — no citation graph for ebooks |
| Generate Structural Skim | ✗ | Hidden |
| Generate Deep Synthesis | ✗ | Hidden |

**Implementation:** A single `is_book` boolean (`paper.content_type == 'book'`) is passed
in the template context and used to conditionally include/exclude sections. No separate
template file — conditional blocks in the existing `detail.html`.

### 2.5 Topic detail page paper list

Books in a topic's paper list show the `book` content-type chip (same as `article` chip
pattern in `papers_list.html`). No other changes — Read Next, not-interesting, seed
toggles all apply to books.

---

## 3. Implementation order

| # | Step | Files |
|---|---|---|
| 1 | Add `ebook_library_dir` to `config.py` | `config.py` |
| 2 | Add `get_ebook_library_dir()` + `save_ebook_pdf()` to `pdf_manager.py` | `services/pdf_manager.py` |
| 3 | UI rename: Papers → Literature in nav, templates, buttons | `base.html`, `papers/list.html`, `papers/detail.html`, `topics/detail.html`, `index.html` |
| 4 | Upload form: Paper/Book toggle + book-specific field visibility (JS show/hide) | `papers/upload_form.html` |
| 5 | Upload route: handle `content_type='book'` — skip LLM, optional PDF, save `onenote_url` | `routes/papers.py` |
| 6 | Literature list: `book` content-type chip in `papers_table.html` and `papers_list.html` | `papers/partials/papers_table.html`, `topics/partials/papers_list.html` |
| 7 | Detail page: `is_book` context var + conditional sections in `detail.html` | `routes/papers.py` (`_paper_detail_context`), `papers/detail.html` |
| 8 | Add `onenote_url` column (migration v6); show/edit in `paper_info.html` and `update_paper_info` route | `db.py`, `routes/papers.py`, `papers/partials/paper_info.html` |

---

## 4. Out of scope

- Route rename (`/papers` → `/literature`) — too much churn for cosmetic gain; URL stays.
- Per-book synthesis skill (books are reference material; no skim/deep path planned).
- Book-specific citation scouting — books are not in the Semantic Scholar graph.
- Book metadata API (e.g. Google Books, Open Library) — manual entry is sufficient.
- Separate "Books" section in the nav — books live in Literature alongside papers/articles.

## 5. Dependencies

- Phase 1b (upload form, paper detail page, paper list page)
- Phase 6 (content_type column already exists from article/author scouting)
- Phase 7 (per-topic detail URL — books use the same `/papers/{id}/topics/{tid}` structure)
