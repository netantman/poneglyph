"""Haiku-based reference extraction from PDF text for papers not indexed by Semantic Scholar."""

import json
import logging
import re

from poneglyph.services.llm import call_haiku

logger = logging.getLogger(__name__)

_MAX_REF_TEXT_CHARS = 30_000  # fallback tail size when no section header found

# Patterns that mark the start of a reference section
_REF_HEADER_RE = re.compile(
    r"(?:^|\n)(References?|Bibliography|Works\s+Cited)\s*[:.]?\s*\n",
    re.IGNORECASE,
)


def _find_ref_section(text: str) -> str:
    """Return the text starting from the last reference section header, or the last 30k chars."""
    m = None
    for m in _REF_HEADER_RE.finditer(text):
        pass  # keep the last match (end-of-document bibliography, not table of contents)
    if m:
        return text[m.start():]
    return text[-_MAX_REF_TEXT_CHARS:] if len(text) > _MAX_REF_TEXT_CHARS else text

_PROMPT = """\
Extract the complete reference list from the text below.
Return a JSON array of objects. Each object must have:
  "title": the paper or book title (string, required — skip entries with no clear title)
  "authors": authors as a single string (string or null)
  "year": publication year as an integer (integer or null)
  "venue": journal, conference, or publisher name (string or null)

If no reference list is found, return [].
Reply with ONLY a valid JSON array, no markdown fences, no commentary.

Text:
{text}"""


def _extract_full_pdf_text(pdf_path: str) -> str | None:
    """Extract all text from a PDF without any character cap."""
    from pathlib import Path
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    path = Path(pdf_path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        return None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return None
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip() or None
    except Exception as exc:
        logger.warning("_extract_full_pdf_text: could not read %s: %s", pdf_path, exc)
        return None


async def extract_references_from_pdf(pdf_path: str) -> list[dict]:
    """Extract a structured reference list from a PDF using Haiku.

    Extracts the full PDF text (no cap) then focuses on the last 20 000 chars
    where reference sections live. Returns a list of dicts with keys:
    title, authors, year, venue. Returns [] on any failure.
    """
    text = _extract_full_pdf_text(pdf_path)
    if not text:
        logger.warning("extract_references_from_pdf: no text from %s", pdf_path)
        return []

    ref_text = _find_ref_section(text)

    raw, err = await call_haiku(_PROMPT.format(text=ref_text), max_tokens=4096)
    if err or not raw:
        logger.warning("extract_references_from_pdf: LLM error for %s: %s", pdf_path, err)
        return []

    clean = re.sub(r"```(?:json)?|```", "", raw).strip()
    start = clean.find("[")
    end = clean.rfind("]")
    if start == -1 or end == -1:
        logger.warning("extract_references_from_pdf: no JSON array in response: %.200s", raw)
        return []

    try:
        refs = json.loads(clean[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("extract_references_from_pdf: JSON parse error: %s", exc)
        return []

    if not isinstance(refs, list):
        return []

    result = []
    for r in refs:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        if not title:
            continue
        result.append({
            "title": title,
            "authors": (r.get("authors") or ""),
            "year": r.get("year"),
            "venue": (r.get("venue") or ""),
        })

    logger.info("extract_references_from_pdf: %d references from %s", len(result), pdf_path)
    return result
