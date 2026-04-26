"""Sonnet deep synthesis — runs the topic's Deep Synthesis skill against a paper's full PDF text.

PDF is strictly required. The call is blocked if:
  - pdf_local_path is not set on the paper
  - pdf_local_path is a remote URL (not a local file)
  - the file does not exist on disk
  - pypdf cannot extract any text (encrypted, image-only, corrupt)
"""

import logging
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from poneglyph.config import settings
from poneglyph.services.llm import call_sonnet, call_sonnet_with_pdf

logger = logging.getLogger(__name__)

# Cap PDF text fed to Sonnet (~80k chars ≈ 20k tokens; enough for most papers)
_MAX_PDF_CHARS = 80_000


# ---------- PDF extraction ----------

def extract_pdf_text(pdf_path: str) -> tuple[str | None, str | None]:
    """Extract text from a local PDF file.

    Returns:
        (text, None)       — success, text is non-empty
        (None, error_msg)  — failure with a human-readable reason
    """
    path = Path(pdf_path)
    if not path.exists():
        return None, f"File not found: {pdf_path}"
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return None, "PDF is encrypted — cannot extract text."
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
        combined = "\n".join(parts).strip()
        if not combined:
            return None, "No selectable text found in PDF (image-only or scanned)."
        return combined[:_MAX_PDF_CHARS], None
    except PdfReadError as exc:
        return None, f"Could not read PDF ({exc})."
    except Exception as exc:
        logger.warning("extract_pdf_text: unexpected error for %s: %s", pdf_path, exc)
        return None, f"Unexpected error reading PDF ({type(exc).__name__})."


# ---------- Prompt template ----------

_PROMPT = """\
{deep_skill_md}

---

## Paper Metadata
Title: {title}
Authors: {authors} ({year})
Venue: {venue}
Abstract: {abstract}

## Research Topic: {topic_name}
Problem statements:
{problems}

Keywords: {keywords}
{notes_section}
## Full Paper Text
{pdf_text}
"""


# ---------- Deep synthesis ----------

async def deep_synthesize(
    paper: dict,
    topic: dict,
    pdf_text: str | None,
    related_notes: list[str] | None = None,
    model: str | None = None,
    pdf_bytes: bytes | None = None,
) -> str:
    """Run the topic's Deep Synthesis skill against the paper's full PDF via Sonnet.

    Args:
        paper: DB paper row dict
        topic: DB topic row dict (must have deep_synthesis_skill_md)
        pdf_text: extracted PDF text (must be non-empty — caller is responsible for the gate)
        related_notes: human_note HTML strings from related papers in the topic

    Returns:
        Non-empty Markdown string.

    Raises:
        ValueError: if the skill is missing or the LLM returns empty output.
    """
    from poneglyph.services.llm_bulk import strip_html  # local import to avoid circular

    skill = (topic.get("deep_synthesis_skill_md") or "").strip()
    if not skill:
        raise ValueError("No Deep Synthesis skill configured for this topic.")

    title = paper.get("title") or ""
    year = (paper.get("published_date") or "")[:4]
    venue = paper.get("published_venue") or ""
    abstract = (paper.get("abstract") or "")[:1500]

    authors = paper.get("authors") or []
    authors_str = ", ".join(authors[:6]) if isinstance(authors, list) else str(authors)

    topic_name = topic.get("name") or ""
    all_kw = (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])
    problems = topic.get("problem_statements") or []
    problems_str = "\n".join(f"- {p}" for p in problems[:5]) or "- (none specified)"
    kw_str = ", ".join(all_kw[:20]) or "(none)"

    notes_section = ""
    if related_notes:
        cleaned = [strip_html(n) for n in related_notes if n]
        cleaned = [c[:500] for c in cleaned if c][:3]
        if cleaned:
            notes_section = (
                "\n## Related paper notes (user context)\n"
                + "\n---\n".join(cleaned)
                + "\n"
            )

    # Escape literal braces in skill/notes so str.format() doesn't choke on them
    safe_skill = skill.replace("{", "{{").replace("}", "}}")
    safe_notes = notes_section.replace("{", "{{").replace("}", "}}")
    prompt = _PROMPT.format(
        deep_skill_md=safe_skill,
        title=title,
        authors=authors_str,
        year=year or "n.d.",
        venue=venue or "—",
        abstract=abstract or "(no abstract available)",
        topic_name=topic_name,
        problems=problems_str,
        keywords=kw_str,
        notes_section=safe_notes,
        pdf_text=pdf_text if pdf_text else "(see attached PDF document)",
    )

    if pdf_bytes is not None:
        result = await call_sonnet_with_pdf(prompt, pdf_bytes, max_tokens=4096, model=model)
    else:
        result = await call_sonnet(prompt, max_tokens=4096, model=model)
    if not result:
        raise ValueError("LLM returned empty response — check API key and model availability.")
    return result
