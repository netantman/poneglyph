"""Extract structured paper metadata from a PDF using Haiku."""

import json
import logging
from pathlib import Path

from poneglyph.services.llm import call_haiku
from poneglyph.services.pdf_manager import extract_pdf_text

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
Extract structured metadata from the academic paper text below.
Return ONLY a valid JSON object with these exact keys:
- title (string)
- authors (list of strings)
- abstract (string — the abstract or executive summary, up to 500 words)
- published_venue (string — journal, conference, working paper series; empty string if none)
- published_date (string — YYYY-MM-DD or YYYY; empty string if unknown)

Paper text (first ~5 pages):
{text}
"""

# Max chars of PDF text to include in the prompt (~6000 chars ≈ ~1500 tokens)
_MAX_TEXT_CHARS = 6000


async def extract_metadata_from_pdf(pdf_path: Path) -> dict:
    """Orchestrate PDF text extraction + Haiku call to return structured metadata.

    Returns a (possibly partial or empty) dict with keys:
    title, authors, abstract, published_venue, published_date.
    Never raises — returns {} on any failure.
    """
    text = extract_pdf_text(pdf_path, max_pages=5)
    if not text:
        logger.info("extract_metadata_from_pdf: no text extracted from %s", pdf_path)
        return {}

    prompt = _PROMPT_TEMPLATE.format(text=text[:_MAX_TEXT_CHARS])
    response, _ = await call_haiku(prompt, max_tokens=512)
    if not response:
        return {}

    try:
        cleaned = response.strip()
        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if not isinstance(data, dict):
            return {}
        # Normalise authors to list[str]
        authors = data.get("authors", [])
        if isinstance(authors, str):
            data["authors"] = [a.strip() for a in authors.split(",") if a.strip()]
        elif not isinstance(authors, list):
            data["authors"] = []
        return data
    except Exception as exc:
        logger.warning(
            "extract_metadata_from_pdf: JSON parse failed: %s — response: %.200s",
            exc,
            response,
        )
        return {}
