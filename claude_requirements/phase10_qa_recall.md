# Phase 10: Q&A Recall — Intent Routing + Two-Stage Triage

**Status: Implemented** (in [`llm_qa.py`](../poneglyph/services/llm_qa.py), [`search.py`](../poneglyph/routes/search.py)).
See "As-built notes" at the bottom for decisions resolved during implementation.

## Problem

The collection-level Q&A ([`llm_qa.py`](../poneglyph/services/llm_qa.py)) is single-shot RAG with a hard cap
of 8 papers (`_TOP_N = 8`). It retrieves once (vector + FTS5), truncates to 8, and feeds only those to
Sonnet with *"answer based only on these papers."*

Symptom: asking *"find all papers relating to corp bond liquidity, transaction costs, slippage, …"* omits
*"Market structure: The last-day rush"* because that paper ranks outside the top 8 for the broad query
embedding — the model never sees it. Naming the paper directly makes FTS5 surface it, so the model can then
answer. **Retrieval recall is the failure, not model reasoning.**

This is fatal for *enumeration* questions ("list all papers about X"), which are recall-complete queries over
the whole collection (452 papers), not top-k lookups.

## Approach (confirmed design decisions)

1. **Intent routing** via a small Haiku classifier — `enumerate` vs `factual`. Robust to phrasing,
   negligible cost/latency. Factual questions keep the existing cheap top-k path.
2. **Enumerate path = two-stage triage** over the **entire candidate set** (title + `main_claim`, ~452 papers,
   est. 20–25k tokens, fits one context): a relevance-filter pass selects every paper that matches "even
   tangentially," then the answer pass produces the cited list over survivors only.
3. **Topic scoping**: if the Q&A is invoked within a topic context, the candidate set is restricted to that
   topic's papers; otherwise it spans the whole collection.

## Design

### 1. Intent classifier — `_classify_intent(question) -> "enumerate" | "factual"`
- Helper in `llm_qa.py`.
- Single Haiku call, `max_tokens=10`, returns one token. Prompt instructs:
  - `enumerate` = the user wants an exhaustive list/survey of matching papers ("find all", "which papers",
    "list every", "what do we have on…").
  - `factual` = a specific question answerable from a handful of papers.
- Fallback to `factual` on any error/ambiguity (preserves current cheap behavior).

### 2. Factual path — `_answer_factual(question, topic_id)`
- Same logic as the original `answer_question`: vector + FTS5, dedup, top-k → `_QA_PROMPT` → Sonnet.
- `_TOP_N` bumped 8 → 12 (cheap, marginal recall gain). **Done.**
- Now topic-scopes both vector and FTS5 retrieval when `topic_id` is set.

### 3. Enumerate path — `_answer_enumeration(question, topic_id) -> str`
**Stage A — candidate assembly (no LLM):**
- Query papers (topic-scoped if `topic_id`, else all) ordered by `id`; join best `main_claim` from
  `topic_paper_notes` via a `ROW_NUMBER() OVER (PARTITION BY paper_id ORDER BY skim_generated_at DESC)` pick.
- Build a compact line per paper: `[{id}] {title} — {main_claim[:150]}`.

**Stage B — relevance triage (LLM):**
- One Sonnet call (`_TRIAGE_PROMPT`, `max_tokens=1024`): given the question + the full candidate list, return
  **only a JSON array of relevant paper IDs** (no reasons — keeps output small/parseable). Parsed with a
  regex + `json.loads`; empty/garbage ⇒ "no matches" message.
- **No chunking** — the full 455-paper candidate list measured at ~22k tokens (avg 193 chars/line), well
  within one context. (Resolves the open question below.)

**Stage C — cited answer (LLM):**
- Survivors re-sorted into triage order; build the richer sections (abstract, main_claim, signal_mechanism,
  deep synthesis, user notes) via shared `_build_rich_section`, then run `_ENUMERATE_ANSWER_PROMPT` (a
  dedicated prompt that asks for ALL papers, grouped by sub-theme, one sentence each, tangential papers
  flagged) → Sonnet with `max_tokens=8192`.
- Stage C is **always run** (resolves the open question). The larger 8192 budget is required: at 4096 the
  list truncated mid-sentence around the 60th paper.

### 4. Routing in `answer_question(question, topic_id=None)`
```
intent = await _classify_intent(question)
if intent == "enumerate":
    return await _answer_enumeration(question, topic_id)
return await _answer_factual(question, topic_id)
```

### 5. Topic plumbing (new wiring)
The search Q&A is collection-wide today; no topic flows into `/ask` or `answer_question`.
- Add optional `topic_id` param to `answer_question(question, topic_id=None)`.
- Add optional `topic_id` form field to `/ask` ([`search.py:65`](../poneglyph/routes/search.py)) and the Q&A
  form in `search.html` — populated only when the page carries a topic context (hidden input). Absent ⇒ global.
- Topic-scope both candidate assembly (enumerate) and the factual retrieval (filter `topic_papers`).

## Cost / latency notes
- Factual questions: +1 tiny Haiku classifier call only.
- Enumerate questions: classifier + 1 triage (large input, small output) + 1 answer call. Acceptable for an
  explicitly exhaustive request.

## Out of scope (possible Phase 11)
- Fully agentic multi-query tool-use loop (option C). Layer on later if multi-hop questions need it.
- Re-ranking models / dedicated retrievers.
- FTS5 robustness: raw user queries with hyphens (e.g. "last-day") break `papers_fts MATCH` with
  `no such column` — the query should be sanitized/quoted before MATCH. Degrades gracefully today (vector
  search still returns results), so deferred.

## As-built notes (decisions resolved during implementation)
- **Candidate scope** = entire collection (or topic's papers if `topic_id` set). Measured ~22k tokens for
  455 papers → no chunking needed; the earlier "token threshold for chunking" open question is moot.
- **Stage C always runs** with `max_tokens=8192` (not skipped for large survivor sets). 4096 truncated the
  list mid-sentence; 8192 completes ~85-paper answers cleanly.
- **Triage output** is IDs-only JSON (no per-paper reasons) to keep it compact and reliably parseable.
- **Prompt brace escaping**: citation-format literals must be written `{{title}}` / `{{paper_id}}` because the
  prompts are built with `str.format()`. Single braces raise `KeyError` and crash *both* paths — regression
  caught and fixed during testing.
- **Topic plumbing** wired through `/ask` (`topic_id` form field) → `answer_question(question, topic_id)` →
  both paths. No template currently passes a `topic_id` (search page is global); the field is ready for a
  future topic-scoped Q&A entry point.

## Verification (manual)
Query: *"Find me all the papers … that relate to … corp bond liquidity … even tangentially."*
- Triage selected 147 / 455 candidates, including the previously-missed papers 546 (last-day rush) and
  547 (Friday rush).
- Final answer cited 85 papers, both 546/547 included, no truncation.
