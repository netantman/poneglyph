"""Per-paper Q&A service — answers a focused question from a single paper's PDF text.

Uses Haiku by default (cheap, fast for short factual answers). Requires a local PDF
with extractable text; no abstract-only fallback (page citations require real page text).
"""

import logging
from pathlib import Path

from poneglyph.config import settings

logger = logging.getLogger(__name__)

# Skim fields to include in the context block (in display order)
_SKIM_FIELDS = [
    ("main_claim", "Main claim"),
    ("signal_mechanism", "Signal mechanism"),
    ("strategy_type", "Strategy type"),
    ("data_source", "Data source"),
    ("data_details", "Data details"),
    ("sample", "Sample"),
    ("universe", "Universe"),
    ("headline_statistic", "Headline statistic"),
    ("portfolio_construction", "Portfolio construction"),
    ("key_metrics", "Key metrics"),
]


def _build_skim_block(skim_notes: dict) -> str:
    lines = []
    for key, label in _SKIM_FIELDS:
        val = (skim_notes.get(key) or "").strip()
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(lines)


def _gate_pdf(paper: dict) -> str | None:
    """Return an error string if any PDF gate fails, else None."""
    pdf_path = paper.get("pdf_local_path") or ""
    if not pdf_path:
        return "No PDF linked to this paper."
    if pdf_path.startswith("http://") or pdf_path.startswith("https://"):
        return "PDF path is a remote URL, not a local file."
    p = Path(pdf_path)
    if not p.exists():
        return f"PDF file not found at: {pdf_path}"
    return None


async def answer_paper_question(
    paper: dict,
    topic: dict,
    question: str,
    skim_notes: dict | None = None,
    deep_synthesis: str | None = None,
    model: str | None = None,
) -> tuple[str, str | None]:
    """Ask a focused question about a single paper using its PDF text.

    Args:
        paper: DB paper row dict (must have pdf_local_path).
        topic: DB topic row dict.
        question: The user's question verbatim.
        skim_notes: topic_paper_notes row for this (paper, topic), or None.
        deep_synthesis: topic_paper_notes.deep_synthesis text, or None.
        model: LLM model ID override. Defaults to haiku_model.

    Returns:
        (answer_markdown, None) on success.
        ("", error_message) on gate failure or API error.
    """
    from poneglyph.services.llm import call_haiku, call_sonnet
    from poneglyph.services.pdf_manager import extract_pdf_text_with_pages

    # Gate: PDF must be a local extractable file
    gate_err = _gate_pdf(paper)
    if gate_err:
        return "", gate_err

    pdf_path = Path(paper["pdf_local_path"])
    pdf_text = extract_pdf_text_with_pages(pdf_path)
    if not pdf_text:
        return "", "Could not extract text from PDF (encrypted or image-only)."

    # Build context blocks
    skim_block = ""
    if skim_notes:
        block = _build_skim_block(skim_notes)
        if block:
            skim_block = (
                "--- Structural skim (what has already been extracted for this topic) ---\n"
                + block
                + "\n\n"
                "Use the structural skim to direct your attention to the relevant parts of "
                "the paper. Do not simply repeat what it says — answer the user's specific "
                "question from the PDF text.\n"
            )

    deep_block = ""
    if deep_synthesis and deep_synthesis.strip():
        deep_block = (
            "--- Deep synthesis (prior analysis for this topic) ---\n"
            + deep_synthesis.strip()[:3000]
            + "\n\n"
            "Use the deep synthesis as additional orientation — it reflects prior analysis "
            "of this paper in this topic's context.\n"
        )

    title = paper.get("title") or "(untitled)"
    year = (paper.get("published_date") or "")[:4] or "n.d."
    authors = paper.get("authors") or []
    authors_str = (
        ", ".join(authors[:6]) if isinstance(authors, list) else str(authors)
    ) or "Unknown authors"
    topic_name = topic.get("name") or ""
    problems = topic.get("problem_statements") or []
    problems_str = "; ".join(str(p) for p in problems[:3])[:300] if problems else ""
    topic_ctx = f"{topic_name}" + (f" — {problems_str}" if problems_str else "")

    truncation_note = (
        "\nNote: the PDF text below is truncated — if the answer is not found, "
        "it may appear in pages not shown.\n"
        if "[PDF truncated" in pdf_text
        else ""
    )

    system_prompt = f"""\
You are a research assistant. Answer the user's question about the paper below using only \
the provided PDF text. Be concise (2–5 sentences). Always cite the specific page number(s) \
where you found the answer, e.g. "(p. 4)" or "(pp. 7–8)".

If the answer is not clearly present in the provided text, say so explicitly — e.g. \
"Not found in the provided text." Do not infer, paraphrase loosely, or construct a \
plausible-sounding answer. A missing page citation or a hedged non-answer is always \
preferable to a fabricated one. The user will use these answers as research notes and \
needs to trust that every page reference is real.

Paper: {title} ({year})
Authors: {authors_str}
Topic context: {topic_ctx}

{skim_block}{deep_block}{truncation_note}
PDF text (page-annotated):
{pdf_text}"""

    use_model = model or settings.haiku_model
    is_sonnet = use_model == settings.sonnet_model

    if is_sonnet:
        from poneglyph.services.llm import call_sonnet
        result = await call_sonnet(
            f"{system_prompt}\n\nQuestion: {question}",
            max_tokens=600,
            model=use_model,
        )
        if not result:
            return "", "LLM returned an empty response — check API key and connectivity."
        return result, None
    else:
        # call_haiku takes a single user prompt string; prepend system as prefix
        full_prompt = f"{system_prompt}\n\nQuestion: {question}"
        result, err = await call_haiku(full_prompt, max_tokens=600)
        if err:
            return "", f"API error: {err}"
        if not result:
            return "", "LLM returned an empty response — check API key and connectivity."
        return result, None
