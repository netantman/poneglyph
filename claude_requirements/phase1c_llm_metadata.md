# Phase 1c: LLM Metadata Extraction from PDF

## Purpose
When a user uploads a PDF (either during paper creation or via "Manage PDF" on the detail page), use the cheapest LLM (Haiku) to extract structured metadata from the PDF content. This auto-populates fields that the user would otherwise fill manually.

## Deliverables
- LLM infrastructure: reusable Haiku call wrapper with API key validation and error handling
- PDF-to-metadata extraction service: send PDF text to Haiku, get back structured fields
- Auto-populate paper fields on PDF upload
- Graceful fallback: if LLM fails or API key not configured, fall back to current behavior (pypdf extraction + manual entry)

## Extracted Fields
- **Title**
- **Authors** (as a list)
- **Abstract / Executive Summary**
- **Published Venue** (journal, conference, working paper series, etc.)
- **Published Date** (year at minimum, full date if available)

## Implementation

### LLM infrastructure (`services/llm.py`)
- [x] Haiku call wrapper: `async def call_haiku(prompt: str, max_tokens: int) -> str`
- [x] Reads `ANTHROPIC_API_KEY` from config/env
- [x] Returns empty string on failure (no exceptions propagated to caller)
- [x] Logs cost/token usage for transparency

### PDF text extraction (`services/pdf_manager.py`)
- [x] `extract_pdf_text(path: Path, max_pages: int = 5) -> str` — extract text from first N pages using pypdf
- [x] Handles encrypted/malformed PDFs gracefully (returns empty string)

### Metadata extraction (`services/llm_metadata.py`)
- [x] `async def extract_metadata_from_pdf(pdf_path: Path) -> dict` — orchestrates text extraction + Haiku call
- [x] Prompt: sends first ~5 pages of text, asks Haiku to return JSON with title, authors, abstract, venue, date
- [x] Parses Haiku JSON response; returns dict with keys matching paper fields
- [x] Returns empty/partial dict on failure — caller merges with any user-provided values

### Integration points

#### Upload form flow
- [x] Metadata source priority (highest to lowest):
  1. **User-provided values** — always take precedence
  2. **Source API metadata** — if paper URL is recognized (e.g. arXiv), use API-fetched metadata (title, authors, abstract, date, venue). No Haiku call needed.
  3. **LLM extraction from PDF** — only if no source API metadata available and user left fields blank
  4. **Auto DOI resolution** — after LLM extraction, if `source=manual` and no URL, search CrossRef by title to auto-populate DOI link
- [x] After PDF file is uploaded and before the paper is created, apply the above priority chain
- [x] Show toast: "Metadata from arXiv" / "Metadata extracted from PDF" / "Could not extract title from PDF — please enter it manually"
- [x] On LLM extraction failure (no title extracted): re-render form with inline message box (not toast), PDF saved to `data/pdfs/tmp/{uuid}.pdf`, `pdf_tmp_id` threaded through hidden input for re-submission
- [x] "Search DOI" button appears next to title field when `pdf_tmp_id` is set — calls `GET /papers/search-by-title` via HTMX, returns result card with "Use this metadata" button
- [x] If CrossRef title search returns no results, show inline message box telling user to fill in fields manually

#### Manage PDF dialog (paper detail page)
- [x] When user uploads a new PDF via the Manage PDF dialog, offer to refresh metadata from the new PDF
- [x] Add a checkbox: "Extract metadata from PDF" (opt-in, shown only when a file is selected)
- [x] If triggered, update paper fields with LLM-extracted values and show toast

### Prompt design
- Keep it minimal — Haiku is cheap but the prompt should be focused
- System prompt: "Extract metadata from the following academic paper text. Return JSON."
- Expected output format:
```json
{
  "title": "...",
  "authors": ["Author One", "Author Two"],
  "abstract": "...",
  "published_venue": "...",
  "published_date": "YYYY-MM-DD or YYYY"
}
```
- Handle edge cases: working papers without venue, preprints, broker research reports (no traditional venue)

## Cost Estimate
- Haiku input: ~2000 tokens for 5 pages of PDF text + prompt
- Haiku output: ~200 tokens for JSON response
- Cost per paper: ~$0.001
- Acceptable for manual uploads (low volume)

## Dependencies
- Phase 1b (paper upload, PDF management, paper detail page)
- Anthropic API key configured in `.env`

## Metadata Source Priority (Design Principle)
Haiku is the **fallback**, not the default. When structured metadata is available from a source API (arXiv, Semantic Scholar, etc.), that data is preferred because it's free, instant, and authoritative. Haiku is only invoked when the paper has no recognized source and the user uploaded a raw PDF without filling in fields. This applies to both manual uploads (Phase 1c) and scouted papers (Phase 2) — scouted papers already have Semantic Scholar metadata and never need Haiku for basic fields.

## What this is NOT
- This is NOT bulk synthesis (Phase 4) — no key insights, trading applications, or recommendations
- This is NOT scouting — no citation graph traversal
- This is purely mechanical metadata extraction to save the user from manual data entry
