"""Haiku bulk synthesis — run the topic's Structural Skim skill against a paper."""

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from poneglyph.services.llm import call_haiku

logger = logging.getLogger(__name__)


# ---------- HTML stripping ----------

class _HTMLStripper(HTMLParser):
    """Strip HTML tags; drop <img> and <figure> elements entirely."""

    _SKIP_TAGS = {"img", "figure", "figcaption", "svg", "script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def result(self) -> str:
        return " ".join(self._parts)


def strip_html(html: str) -> str:
    """Return plain text from HTML; drops images entirely."""
    if not html:
        return ""
    parser = _HTMLStripper()
    parser.feed(html)
    return parser.result()


# ---------- PDF section extraction ----------

# Heading patterns for common section names
_SECTION_PATTERNS = {
    "abstract":     re.compile(r"^\s*abstract\s*$", re.I | re.M),
    "introduction": re.compile(r"^\s*(?:1[\.\s]+)?introduction\s*$", re.I | re.M),
    "conclusion":   re.compile(r"^\s*(?:\d+[\.\s]+)?conclusions?\s*(?:and\s+\w+)?\s*$", re.I | re.M),
}

# Caption lines: "Figure 1:", "Table 2 —", etc.
_CAPTION_RE = re.compile(r"^((?:fig(?:ure)?|table)\.?\s*\d+[.:—\s].{10,200})$", re.I | re.M)

_MAX_SKIM_CHARS = 12_000


def _extract_section(text: str, pattern: re.Pattern, max_chars: int = 3000) -> str:
    """Return up to max_chars of text starting from the first line matching pattern."""
    m = pattern.search(text)
    if not m:
        return ""
    start = m.end()
    # Stop at the next section heading (a short all-caps or title-case line)
    next_heading = re.search(r"\n[A-Z][A-Z\s]{2,50}\n|\n\d+[\.\s]+[A-Z]", text[start:])
    end = start + (next_heading.start() if next_heading else max_chars)
    return text[start:end].strip()[:max_chars]


def extract_skim_sections(pdf_path: str) -> tuple[str, bool]:
    """Extract abstract, introduction, conclusion and figure/table captions from a PDF.

    Returns:
        (text, is_pdf)  — is_pdf=True when PDF sections were used, False on fallback.
    """
    path = Path(pdf_path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        return "", False

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return "", False

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return "", False

        pages_text = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t)

        full_text = "\n".join(pages_text).strip()
        if not full_text:
            return "", False

    except Exception as exc:
        logger.warning("extract_skim_sections: could not read %s: %s", pdf_path, exc)
        return "", False

    parts: list[str] = []

    abstract = _extract_section(full_text, _SECTION_PATTERNS["abstract"], 1500)
    if abstract:
        parts.append(f"[Abstract]\n{abstract}")

    intro = _extract_section(full_text, _SECTION_PATTERNS["introduction"], 3000)
    if intro:
        parts.append(f"[Introduction]\n{intro}")

    conclusion = _extract_section(full_text, _SECTION_PATTERNS["conclusion"], 3000)
    if conclusion:
        parts.append(f"[Conclusion]\n{conclusion}")

    # Figure and table captions (up to 20)
    captions = _CAPTION_RE.findall(full_text)
    if captions:
        caption_block = "\n".join(c.strip() for c in captions[:20])
        parts.append(f"[Key Figure / Table Captions]\n{caption_block}")

    if not parts:
        # Fell back: no recognisable sections found — use first 3000 chars
        parts.append(f"[Paper text — section headings not detected]\n{full_text[:3000]}")

    combined = "\n\n".join(parts)
    return combined[:_MAX_SKIM_CHARS], True


# ---------- Synthesis ----------

_VALID_RECS = {"read", "skip", "deep_dive"}

_PROMPT_WRAPPER = """\
{skim_skill_md}

---

## Paper
Title: {title}
Authors: {authors} ({year})
Venue: {venue}

## Paper Content ({content_source})
{paper_content}

## Research Topic: {topic_name}
Problem statements:
{problems}

Keywords: {keywords}
{notes_section}
Reply with ONLY a valid JSON object (no markdown fences, no commentary):
{{
  "main_claim": "2-3 sentences",
  "data_source": "source and period",
  "strategy_type": "type",
  "headline_statistic": "stat or empty string if not found",
  "signal_mechanism": "mechanism description",
  "data_details": "specific sources",
  "sample": "period, frequency, asset class, geography",
  "universe": "description of universe",
  "portfolio_construction": "method description",
  "key_tables": ["Table 1: Main results", "Table 3: Robustness"],
  "key_metrics": "metrics mentioned",
  "recommendation": "read|skip|deep_dive"
}}

If a field cannot be determined from the available text, use an empty string (or empty list for key_tables).\
"""

_SKIM_FIELDS = (
    "main_claim", "data_source", "strategy_type", "headline_statistic",
    "signal_mechanism", "data_details", "sample", "universe",
    "portfolio_construction", "key_tables", "key_metrics",
)


async def synthesize_paper(
    paper: dict, topic: dict, related_notes: list[str] | None = None
) -> tuple[dict, str]:
    """Run the topic's Structural Skim skill against a paper via Haiku.

    Uses PDF sections (abstract, introduction, conclusion, captions) when a local PDF is
    available and readable. Falls back to abstract-only and notes this in the prompt.

    Returns (result_dict, error_str). On success error_str is "". On failure result_dict is {}.
    Returns ({}, "") — not an error — when the topic has no skim_skill_md set.
    """
    skim_skill_md = (topic.get("skim_skill_md") or "").strip()
    if not skim_skill_md:
        logger.warning("synthesize_paper: topic '%s' has no skim_skill_md — skipping", topic.get("name", "?"))
        return {}, ""

    title = paper.get("title") or ""
    year = (paper.get("published_date") or "")[:4]
    venue = paper.get("published_venue") or ""

    authors = paper.get("authors") or []
    authors_str = ", ".join(authors[:6]) if isinstance(authors, list) else str(authors)

    # --- Paper content: PDF sections if available, else abstract ---
    pdf_path = (paper.get("pdf_local_path") or "").strip()
    is_pdf = False
    paper_content = ""

    if pdf_path and not pdf_path.startswith("http"):
        paper_content, is_pdf = extract_skim_sections(pdf_path)

    if not paper_content:
        # Fallback: abstract only
        abstract = (paper.get("abstract") or "").strip()
        paper_content = abstract[:2000] if abstract else "(no abstract available)"
        is_pdf = False

    content_source = "PDF: abstract + introduction + conclusion + captions" if is_pdf else "abstract only — no PDF available"

    # --- Topic context ---
    topic_name = topic.get("name") or ""
    all_kw = (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])
    problems = topic.get("problem_statements") or []
    problems_str = "\n".join(f"- {p}" for p in problems[:5]) if problems else "- (none specified)"
    kw_str = ", ".join(all_kw[:20]) if all_kw else "(none)"

    notes_section = ""
    if related_notes:
        cleaned = [strip_html(n) for n in related_notes if n]
        cleaned = [c[:400] for c in cleaned if c][:3]
        if cleaned:
            notes_section = (
                "\n## Related paper notes (user context)\n"
                + "\n---\n".join(cleaned)
                + "\n"
            )

    prompt = _PROMPT_WRAPPER.format(
        skim_skill_md=skim_skill_md,
        title=title,
        authors=authors_str,
        year=year or "n.d.",
        venue=venue or "—",
        paper_content=paper_content,
        content_source=content_source,
        topic_name=topic_name,
        problems=problems_str,
        keywords=kw_str,
        notes_section=notes_section,
    )

    raw, api_error = await call_haiku(prompt, max_tokens=1024)
    if api_error:
        return {}, api_error
    if not raw:
        return {}, "Haiku returned an empty response — the paper may have no abstract or the prompt was too long"

    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        # Extract only the {...} span so trailing model commentary doesn't break parsing
        start = clean.find("{")
        end = clean.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise json.JSONDecodeError("no JSON object found", clean, 0)
        data = json.loads(clean[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("synthesize_paper JSON parse failed: %s — raw: %.300s", exc, raw)
        return {}, f"Model output was not valid JSON (parse error: {exc}) — try regenerating"

    if not isinstance(data, dict):
        return {}, "Model returned an unexpected response format — try regenerating"

    rec = data.get("recommendation", "skip")
    if rec not in _VALID_RECS:
        rec = "skip"

    key_tables = data.get("key_tables") or []
    if isinstance(key_tables, str):
        key_tables = [key_tables]

    result: dict = {"skim_recommendation": rec, "pdf_used": is_pdf}
    for field in _SKIM_FIELDS:
        if field == "key_tables":
            result[field] = key_tables
        else:
            result[field] = str(data.get(field) or "")

    return result, ""
