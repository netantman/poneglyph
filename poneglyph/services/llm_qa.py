"""Q&A over the paper collection.

Two paths depending on intent:
  - factual: vector + FTS5 retrieval → top-k → Sonnet answer (original behaviour)
  - enumerate: full candidate list → Sonnet triage → Sonnet cited answer

Intent is classified by a cheap Haiku call. Falls back to factual on any error.
"""

import json
import logging
import re

from poneglyph.db import fetch_all, fetch_one, row_to_dict
from poneglyph.services.llm import call_haiku, call_sonnet
from poneglyph.services.llm_bulk import strip_html

logger = logging.getLogger(__name__)

_TOP_N = 12  # papers fed to Sonnet on the factual path


# ---------- shared retrieval helpers ----------

def _vector_search(query: str, top_k: int, topic_id: int | None = None) -> list[dict]:
    try:
        from poneglyph.services.relevance import semantic_search
        results = semantic_search(query, top_k=top_k)
        if topic_id:
            ids = _topic_paper_ids(topic_id)
            results = [r for r in results if r.get("id") in ids]
        return results
    except Exception as exc:
        logger.warning("llm_qa: vector search failed: %s", exc)
        return []


def _keyword_search(query: str, top_k: int, topic_id: int | None = None) -> list[dict]:
    try:
        if topic_id:
            rows = fetch_all(
                """SELECT p.* FROM papers p
                   JOIN papers_fts fts ON p.id = fts.rowid
                   JOIN topic_papers tp ON p.id = tp.paper_id
                   WHERE papers_fts MATCH ? AND tp.topic_id = ?
                   ORDER BY rank LIMIT ?""",
                (query, topic_id, top_k),
            )
        else:
            rows = fetch_all(
                """SELECT p.* FROM papers p
                   JOIN papers_fts fts ON p.id = fts.rowid
                   WHERE papers_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, top_k),
            )
        return [row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("llm_qa: keyword search failed: %s", exc)
        return []


def _topic_paper_ids(topic_id: int) -> set[int]:
    rows = fetch_all("SELECT paper_id FROM topic_papers WHERE topic_id = ?", (topic_id,))
    return {r["paper_id"] for r in rows}


def _all_deep_syntheses(paper_id: int) -> list[tuple[str, str]]:
    rows = fetch_all(
        """SELECT t.name, tpn.deep_synthesis
           FROM topic_paper_notes tpn
           JOIN topics t ON t.id = tpn.topic_id
           WHERE tpn.paper_id = ? AND tpn.deep_synthesis IS NOT NULL AND tpn.deep_synthesis != ''""",
        (paper_id,),
    )
    return [(r["name"], r["deep_synthesis"]) for r in rows]


def _best_skim(paper_id: int) -> dict | None:
    rows = fetch_all(
        """SELECT tpn.*
           FROM topic_paper_notes tpn
           WHERE tpn.paper_id = ?
           ORDER BY
               CASE tpn.skim_recommendation
                   WHEN 'deep_dive' THEN 0
                   WHEN 'read' THEN 1
                   ELSE 2
               END,
               tpn.skim_generated_at DESC
           LIMIT 1""",
        (paper_id,),
    )
    return row_to_dict(rows[0]) if rows else None


def _build_rich_section(paper: dict) -> str:
    pid = paper.get("id")
    title = paper.get("title") or f"Paper {pid}"
    year = (paper.get("published_date") or "")[:4]
    venue = paper.get("published_venue") or ""
    abstract = (paper.get("abstract") or "")[:600]

    sec = f"**[{title}](/papers/{pid})** ({year or 'n.d.'}{(', ' + venue) if venue else ''})\n"
    if abstract:
        sec += f"Abstract: {abstract}\n"

    skim = _best_skim(pid)
    if skim:
        if skim.get("main_claim"):
            sec += f"Main claim: {skim['main_claim']}\n"
        if skim.get("signal_mechanism"):
            sec += f"Signal/mechanism: {skim['signal_mechanism']}\n"

    for topic_name, deep_synth in _all_deep_syntheses(pid):
        sec += f"Deep synthesis ({topic_name}): {deep_synth}\n"

    note_rows = fetch_all(
        "SELECT human_note FROM topic_paper_notes WHERE paper_id = ?"
        " AND human_note IS NOT NULL AND human_note != ''",
        (pid,),
    )
    if note_rows:
        clean = strip_html(" ".join(r["human_note"] for r in note_rows))[:300]
        if clean:
            sec += f"User annotation: {clean}\n"

    return sec


_QA_PROMPT = """\
You are a research assistant with access to the papers below. Answer the user's question \
based only on these papers. For every claim you make, cite the paper with an inline \
Markdown hyperlink using the format [{{title}}](/papers/{{paper_id}}). You may cite multiple \
papers per sentence.

If the papers do not contain enough information to answer the question, say so explicitly.

---

Question: {question}

---

## Papers ({n_papers}):

{paper_sections}

---

Provide a concise, accurate answer with inline citation links throughout.
"""


# ---------- intent classification ----------

_INTENT_PROMPT = """\
Classify this question as either "enumerate" or "factual".

"enumerate" = the user wants an exhaustive list or survey of ALL papers in the database \
that relate to a topic, even tangentially. Signals: "find all", "list all", "which papers", \
"what papers do we have on", "find me every", "list every", or any request to be comprehensive.

"factual" = a specific question that can be answered from a handful of relevant papers.

Reply with exactly one word: enumerate  OR  factual

Question: {question}
"""


async def _classify_intent(question: str) -> str:
    """Return 'enumerate' or 'factual'. Falls back to 'factual' on any error."""
    try:
        text, err = await call_haiku(_INTENT_PROMPT.format(question=question), max_tokens=10)
        if err or not text:
            return "factual"
        token = text.strip().lower()
        return "enumerate" if "enumerate" in token else "factual"
    except Exception as exc:
        logger.warning("llm_qa: intent classification failed: %s", exc)
        return "factual"


# ---------- enumerate path ----------

_TRIAGE_PROMPT = """\
You are a research librarian. Below is a numbered list of papers (ID, title, main claim).
Your task: identify every paper that is relevant to the query — even tangentially.

Return ONLY a JSON array of paper IDs (integers) that are relevant. No explanation.
If none are relevant, return an empty array: []

Query: {question}

Papers:
{candidate_lines}
"""

_ENUMERATE_ANSWER_PROMPT = """\
You are a research assistant. The user asked:

"{question}"

Below are the papers from the database that are relevant to this query. \
For each paper, cite it with an inline Markdown hyperlink: [{{title}}](/papers/{{paper_id}}).

List ALL relevant papers clearly, grouped by sub-theme if there are natural groupings. \
For each paper give one sentence explaining why it is relevant. \
If a paper is only tangentially related, still include it but note the tangential connection.

---

## Relevant Papers ({n_papers}):

{paper_sections}

---

Produce the full list with one sentence per paper and inline citation links.
"""


async def _answer_enumeration(question: str, topic_id: int | None) -> str:
    """Two-stage enumerate: triage full candidate list, then produce cited answer."""

    # Stage A: assemble candidates (title + best main_claim)
    if topic_id:
        rows = fetch_all(
            """SELECT p.id, p.title, p.published_date, p.abstract,
                      p.published_venue, p.authors
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               ORDER BY p.id""",
            (topic_id,),
        )
    else:
        rows = fetch_all(
            """SELECT id, title, published_date, abstract,
                      published_venue, authors
               FROM papers ORDER BY id"""
        )

    candidates = [row_to_dict(r) for r in rows]
    if not candidates:
        return "No papers found in the database."

    # Build compact candidate lines for triage
    claim_map: dict[int, str] = {}
    skim_rows = fetch_all(
        """SELECT paper_id, main_claim,
                  ROW_NUMBER() OVER (PARTITION BY paper_id ORDER BY skim_generated_at DESC) rn
           FROM topic_paper_notes WHERE main_claim IS NOT NULL"""
    )
    for r in skim_rows:
        if r["rn"] == 1:
            claim_map[r["paper_id"]] = r["main_claim"]

    candidate_lines = "\n".join(
        f"[{p['id']}] {(p['title'] or '').strip()} — {claim_map.get(p['id'], '')[:150]}"
        for p in candidates
    )

    # Stage B: triage via Sonnet
    triage_prompt = _TRIAGE_PROMPT.format(
        question=question,
        candidate_lines=candidate_lines,
    )
    triage_raw = await call_sonnet(triage_prompt, max_tokens=1024)
    if not triage_raw:
        return "Could not complete the search — check API availability."

    # Parse JSON array of IDs
    try:
        match = re.search(r"\[[\s\S]*?\]", triage_raw)
        survivor_ids: list[int] = json.loads(match.group()) if match else []
    except Exception:
        survivor_ids = []

    if not survivor_ids:
        return (
            "No papers in the database appear to match your query, even tangentially. "
            "Try rephrasing or broadening the topic."
        )

    # Stage C: build rich sections for survivors and produce cited answer
    id_set = {int(i) for i in survivor_ids}
    survivors = [p for p in candidates if p["id"] in id_set]
    # Preserve triage order
    id_order = {pid: idx for idx, pid in enumerate(survivor_ids)}
    survivors.sort(key=lambda p: id_order.get(p["id"], 9999))

    sections = [_build_rich_section(p) for p in survivors]
    paper_sections = "\n\n---\n\n".join(sections)

    answer_prompt = _ENUMERATE_ANSWER_PROMPT.format(
        question=question,
        n_papers=len(survivors),
        paper_sections=paper_sections,
    )
    result = await call_sonnet(answer_prompt, max_tokens=8192)
    if not result:
        return "Could not generate the answer — check API availability."
    return result


# ---------- factual path ----------

async def _answer_factual(question: str, topic_id: int | None) -> str:
    vec_results = _vector_search(question, top_k=_TOP_N, topic_id=topic_id)
    kw_results = _keyword_search(question, top_k=_TOP_N, topic_id=topic_id)

    seen: set[int] = set()
    combined: list[dict] = []
    for p in vec_results + kw_results:
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            combined.append(p)

    combined = combined[:_TOP_N]

    if not combined:
        return (
            "No papers found matching your question. "
            "Try adding papers to your topics or running scouting first."
        )

    sections = [_build_rich_section(p) for p in combined]
    paper_sections = "\n\n---\n\n".join(sections)

    prompt = _QA_PROMPT.format(
        question=question,
        n_papers=len(combined),
        paper_sections=paper_sections,
    )
    result = await call_sonnet(prompt, max_tokens=2048)
    if not result:
        return "Could not get an answer — check your Anthropic API key and model availability."
    return result


# ---------- public entry point ----------

async def answer_question(question: str, topic_id: int | None = None) -> str:
    """Route to factual or enumerate path based on Haiku intent classification."""
    intent = await _classify_intent(question)
    logger.info("llm_qa: intent=%s question=%r topic_id=%s", intent, question[:80], topic_id)
    if intent == "enumerate":
        return await _answer_enumeration(question, topic_id)
    return await _answer_factual(question, topic_id)
