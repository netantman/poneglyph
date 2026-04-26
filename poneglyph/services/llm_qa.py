"""Q&A over the paper collection: vector similarity + FTS5 retrieval → Sonnet answer."""

import logging

from poneglyph.db import fetch_all, fetch_one, row_to_dict
from poneglyph.services.llm import call_sonnet
from poneglyph.services.llm_bulk import strip_html

logger = logging.getLogger(__name__)

_TOP_N = 8  # max papers fed to Sonnet


def _vector_search(query: str, top_k: int) -> list[dict]:
    try:
        from poneglyph.services.relevance import semantic_search
        return semantic_search(query, top_k=top_k)
    except Exception as exc:
        logger.warning("llm_qa: vector search failed: %s", exc)
        return []


def _keyword_search(query: str, top_k: int) -> list[dict]:
    try:
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


def _all_deep_syntheses(paper_id: int) -> list[tuple[str, str]]:
    """Return (topic_name, deep_synthesis) for all topics that have one for this paper."""
    rows = fetch_all(
        """SELECT t.name, tpn.deep_synthesis
           FROM topic_paper_notes tpn
           JOIN topics t ON t.id = tpn.topic_id
           WHERE tpn.paper_id = ? AND tpn.deep_synthesis IS NOT NULL AND tpn.deep_synthesis != ''""",
        (paper_id,),
    )
    return [(r["name"], r["deep_synthesis"]) for r in rows]


def _best_skim(paper_id: int) -> dict | None:
    """Return the best available skim for a paper (prefer deep_dive, then read, then any)."""
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


async def answer_question(question: str) -> str:
    """Answer a question using vector + FTS5 retrieval and a Sonnet call.

    Returns a Markdown string with inline hyperlinks to paper detail pages.
    """
    vec_results = _vector_search(question, top_k=_TOP_N)
    kw_results = _keyword_search(question, top_k=_TOP_N)

    # Merge, dedup by paper ID (vector results ranked first)
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

    sections: list[str] = []
    for paper in combined:
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

        note_row = fetch_one("SELECT human_note FROM paper_notes WHERE paper_id = ?", (pid,))
        if note_row and note_row["human_note"]:
            clean = strip_html(note_row["human_note"])[:300]
            if clean:
                sec += f"User annotation: {clean}\n"

        sections.append(sec)

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
