# Phase 4c: Per-Paper Q&A

## Goal

Allow the user to ask targeted questions about a specific paper — "how exactly is conditional
variance computed?", "what's the sample period?" — and get a short answer with a page citation
pulled directly from the PDF. Selected Q&A pairs can be appended to the per-topic human note
for later reference.

This is distinct from the cross-paper Q&A on the `/search` page (Phase 4), which retrieves
across all papers. This feature is strictly single-paper, single-question, PDF-grounded.

## Trigger policy

User-initiated only, from the paper detail page. Requires a local PDF with extractable text
(same gates as deep synthesis). No auto-fire, no scheduled path.

## Data model

### New table: `paper_qa_history`

```sql
CREATE TABLE IF NOT EXISTS paper_qa_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_paper_qa_history_paper_topic
    ON paper_qa_history (paper_id, topic_id);
```

Q&A pairs are scoped to `(paper_id, topic_id)` so each topic tab has its own history. Deleting
a paper or topic cascades to delete its Q&A rows. No UNIQUE constraint on question — the user
can ask the same question twice and compare answers.

## Routes

```
GET  /papers/{paper_id}/topics/{topic_id}/qa          → render Q&A panel partial (history + input box)
POST /papers/{paper_id}/topics/{topic_id}/qa          → run Q&A; return updated panel (question + answer prepended)
POST /papers/{paper_id}/topics/{topic_id}/qa/{qa_id}/save-to-note  → append Q&A pair to per-topic human note
DELETE /papers/{paper_id}/topics/{topic_id}/qa/{qa_id}             → delete one history item
```

All routes return HTML partials; the panel is an htmx target that replaces in-place.

## PDF extraction — page-annotated text

The key difference from deep synthesis: the PDF text must carry **page markers** so Sonnet can
cite specific pages in its answer.

New helper in `services/pdf_manager.py`:

```python
def extract_pdf_text_with_pages(path: Path, max_chars: int = 80_000) -> str:
    """
    Extract full PDF text with inline [Page N] markers.
    Truncates to max_chars from the start (Q&A needs the full body, not head+tail).
    Returns empty string on failure.
    """
```

Format of the returned string:

```
[Page 1]
… page 1 text …

[Page 2]
… page 2 text …
```

Truncation: if the full text exceeds `max_chars`, stop at the last complete page boundary before
the limit. Log a warning that the PDF was truncated and include a note in the system prompt so
Sonnet can flag "not in the portion I was given" when appropriate.

The existing `extract_pdf_text` (head+tail for structural skim) and `extract_pdf_text` in
`llm_deep.py` are unchanged — this is a separate helper for Q&A only.

## LLM service: `services/llm_qa_paper.py`

New file, distinct from `llm_qa.py` (which does cross-paper search).

```python
async def answer_paper_question(
    paper: dict,
    topic: dict,
    question: str,
    pdf_text_with_pages: str,
    skim_notes: dict | None,
    deep_synthesis: str | None,
) -> str:
    """
    Ask a focused question about a single paper.
    skim_notes: the topic_paper_notes row for this (paper, topic), or None.
    deep_synthesis: topic_paper_notes.deep_synthesis for this pair, or None.
    Returns a Markdown string: answer + page citation(s).
    Returns an error string (prefixed '**Error:**') on API failure.
    """
```

### Context assembly

Before building the prompt, the route handler fetches the `topic_paper_notes` row for this
`(paper_id, topic_id)`. This supplies two optional context blocks passed into
`answer_paper_question`:

- **`skim_notes`**: the structured skim fields (`main_claim`, `signal_mechanism`,
  `data_details`, `sample`, `portfolio_construction`, etc.) serialised as a compact
  key-value block. Included when the row exists and at least one skim field is non-null.
- **`deep_synthesis`**: the free-text deep synthesis Markdown. Included when non-null.

Both are optional — the route does not block the Q&A if neither exists yet.

### Prompt design

**System prompt:**
```
You are a research assistant. Answer the user's question about the paper below using only
the provided PDF text. Be concise (2–5 sentences). Always cite the specific page number(s)
where you found the answer, e.g. "(p. 4)" or "(pp. 7–8)".

If the answer is not clearly present in the provided text, say so explicitly — e.g. "Not
found in the provided text." Do not infer, paraphrase loosely, or construct a plausible-
sounding answer. A missing page citation or a hedged non-answer is always preferable to a
fabricated one. The user will use these answers as research notes and needs to trust that
every page reference is real.

Paper: {title} ({year})
Authors: {authors}
Topic context: {topic.name} — {topic.problem_statements[:300]}

--- Structural skim (what has already been extracted for this topic) ---
{skim_notes_block}          ← omitted if no skim exists yet

--- Deep synthesis (prior analysis for this topic) ---
{deep_synthesis_block}      ← omitted if no deep synthesis exists yet

Use the structural skim and deep synthesis as a guide for which parts of the paper are most
relevant to this topic. They were produced by a prior pass and tell you what the key claims,
mechanisms, and data details are — use them to direct your attention when searching the PDF
text. Do not simply repeat what they say; answer the user's specific question from the PDF.

PDF text (page-annotated):
{pdf_text_with_pages}
```

The skim block is formatted as a flat key-value list, e.g.:

```
Main claim: Bond return predictability driven by conditional variance of lagged bond returns.
Signal mechanism: Time-series: conditional variance (realized variance) of lagged bond market returns predicts next-month aggregate bond returns.
Data details: Monthly US Treasury returns 1952–2021.
Sample: All CRSP bonds.
...
```

Fields with null/empty values are omitted to keep the block compact.

**User message:** The user's question verbatim.

**Model:** `claude-haiku-4-5` — Q&A answers are short and factual; Haiku is fast and cheap.
Use `claude-sonnet-4-6` only if the user explicitly requests it via a model toggle (see UI).

**Max output tokens:** 400. Q&A answers should be brief with a citation, not a mini-synthesis.

**Gate logic** (same as deep synthesis, return error string if any gate fails):
1. `paper.pdf_local_path` is not set → "No PDF linked to this paper."
2. Path is a URL, not a local file → "PDF path is a remote URL, not a local file."
3. File does not exist on disk → "PDF file not found at {path}."
4. `extract_pdf_text_with_pages` returns empty string → "Could not extract text from PDF (encrypted or image-only)."

## UI

### Q&A box on the paper detail page

The Q&A panel lives in a new **collapsible `<details>` section** at the bottom of the
per-topic paper detail page (`/papers/{id}/topics/{tid}`), below Deep Synthesis and above
the Human Note. It is **always visible on the per-topic page** — no tab click required.

```
┌─────────────────────────────────────────────────────┐
│ ▸ Ask about this paper                              │  ← <details> toggle, open by default
│                                                     │
│  [________________________________________] [Ask]   │  ← text input + submit button
│  ○ Haiku  ○ Sonnet                                  │  ← model toggle (small radio, below input)
│                                                     │
│  ── History ─────────────────────────────────────── │
│  Q: How is conditional variance computed?           │
│  A: The paper uses a 22-day rolling window of       │
│     squared daily excess returns... (p. 4)          │
│  [→ Add to note]  [×]                               │
│                                                     │
│  Q: What is the sample period?                      │
│  A: January 1990 – December 2022. (p. 2)            │
│  [→ Add to note]  [×]                               │
└─────────────────────────────────────────────────────┘
```

- `<details open>` — expanded by default on page load.
- Submitting the form fires `POST /papers/{id}/topics/{tid}/qa` (htmx `hx-post`, `hx-target="#paper-qa-panel"`, `hx-swap="outerHTML"`). The response re-renders the whole panel (input cleared, new answer prepended to history).
- The spinner / disabled state fires while the request is in flight (`hx-indicator`).
- History is ordered newest-first. The last 20 items are shown; older ones are hidden behind a "Show N more" toggle (JS, same pattern as recently-asked on the search page).
- **"→ Add to note"** fires `POST /papers/{id}/topics/{tid}/qa/{qa_id}/save-to-note` (htmx OOB: button swaps to "✓ Added" for 2s then reverts; the human note textarea content is NOT reloaded — the append is server-side only). The format appended to the note:

```markdown
**Q:** How is conditional variance computed?
**A:** The paper uses a 22-day rolling window of squared daily excess returns... (p. 4)
```

- **[×]** fires `DELETE /papers/{id}/topics/{tid}/qa/{qa_id}`; removes the row from DB and from the DOM (htmx `hx-swap="outerHTML"` with an empty response).
- If PDF gates fail, the answer area shows an inline error message (same styling as other gate errors) instead of an answer card.

### Template files

- `templates/papers/partials/paper_qa.html` — the full panel (input box + history list)
- `templates/papers/partials/paper_qa_item.html` — a single Q&A history row (question, answer, Add/Delete buttons)

The panel is included in `templates/papers/detail.html` via:

```jinja
{% include "papers/partials/paper_qa.html" %}
```

populated from `GET /papers/{paper_id}/topics/{topic_id}` which already passes `active_topic_id`.

## "Add to note" format in the human note

The note uses Quill rich text (stored as HTML). The save-to-note route appends the Q&A block
to the existing `topic_paper_notes.human_note` HTML as:

```html
<p><strong>Q:</strong> {question}</p>
<p><strong>A:</strong> {answer}</p>
<hr>
```

The existing Quill editor in the human note modal will render this correctly. The append
is an idempotent `UPDATE` — saving the note again in Quill will overwrite with whatever
the user has at that point, so the Q&A entry is preserved until they explicitly delete it
in Quill.

## Implementation order

| # | Step | Notes |
|---|---|---|
| 1 | DB migration: `paper_qa_history` table + index | In `db.py` `_migrate()` |
| 2 | `extract_pdf_text_with_pages` in `pdf_manager.py` | Page-annotated extraction, 80k char cap |
| 3 | `services/llm_qa_paper.py`: `answer_paper_question` | Gate logic + Haiku prompt + skim/deep context blocks |
| 4 | Routes: GET panel, POST ask (fetches `topic_paper_notes` for context), POST save-to-note, DELETE item | In `papers.py` |
| 5 | Templates: `paper_qa.html` + `paper_qa_item.html` | Panel + individual item partial |
| 6 | Wire panel into `detail.html` | Include in per-topic detail template |

## Out of scope

- Multi-turn conversation / follow-up questions — each question is independent; no conversation
  history sent to the LLM. The user's human note is the accumulation layer.
- Cross-paper Q&A — that's the search-page Q&A (Phase 4).
- Q&A without a PDF — abstract-only fallback deliberately excluded; the value of page citations
  requires a real PDF.
- Exporting Q&A history separately — the "Add to note" flow is the export path.

## Dependencies

- Phase 1 (DB, paper detail page)
- Phase 7 (per-topic canonical URL `/papers/{id}/topics/{tid}` — Q&A is topic-scoped)
- `pdf_manager.py` (extended with `extract_pdf_text_with_pages`)
- Local PDF with extractable text (same requirement as deep synthesis)
