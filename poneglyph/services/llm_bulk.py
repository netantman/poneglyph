"""Sonnet bulk synthesis — run the topic's Structural Skim skill against a paper."""

import base64
import logging
from html.parser import HTMLParser
from pathlib import Path

from poneglyph.services.llm import get_client

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

_MAX_SKIM_CHARS = 12_000
_HEAD_CHARS = 8_000
_TAIL_CHARS = 4_000


def extract_skim_sections(pdf_path: str) -> tuple[str, bool]:
    """Extract text from a PDF for structural skim using head+tail sampling.

    Returns the first 8 000 chars (opening/exec summary) and last 4 000 chars
    (conclusions/recommendations), labelled [Opening] and [Closing]. This is robust
    to OCR artifacts and non-academic structures like bank research reports.

    Returns:
        (text, is_pdf) — is_pdf=True when PDF was read, False on fallback.
    """
    path = Path(pdf_path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        return "", False

    try:
        from pypdf import PdfReader
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

    if len(full_text) <= _MAX_SKIM_CHARS:
        return full_text, True

    head = full_text[:_HEAD_CHARS]
    tail = full_text[-_TAIL_CHARS:]
    combined = f"[Opening]\n{head}\n\n[Closing]\n{tail}"
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
Use the record_skim tool to record your findings. \
For any field you cannot determine from the available text, use an empty string (or empty list for key_tables).\
"""

_SKIM_FIELDS = (
    "main_claim", "data_source", "strategy_type", "headline_statistic",
    "signal_mechanism", "data_details", "sample", "universe",
    "portfolio_construction", "key_tables", "key_metrics", "skip_reason",
)

_SKIM_TOOL = {
    "name": "record_skim",
    "description": "Record the structural skim results for a paper.",
    "input_schema": {
        "type": "object",
        "properties": {
            "main_claim": {"type": "string"},
            "data_source": {"type": "string"},
            "strategy_type": {"type": "string"},
            "headline_statistic": {"type": "string"},
            "signal_mechanism": {"type": "string"},
            "data_details": {"type": "string"},
            "sample": {"type": "string"},
            "universe": {"type": "string"},
            "portfolio_construction": {"type": "string"},
            "key_tables": {"type": "array", "items": {"type": "string"}},
            "key_metrics": {"type": "string"},
            "recommendation": {"type": "string", "enum": ["read", "skip", "deep_dive"]},
            "skip_reason": {
                "type": "string",
                "description": "One concrete sentence stating the specific problem statement, methodology flaw, or scope mismatch that disqualifies this paper. Required when recommendation is 'skip'; use an empty string for 'read' or 'deep_dive'.",
            },
        },
        "required": [
            "main_claim", "data_source", "strategy_type", "headline_statistic",
            "signal_mechanism", "data_details", "sample", "universe",
            "portfolio_construction", "key_tables", "key_metrics", "recommendation",
            "skip_reason",
        ],
    },
}


async def synthesize_paper(
    paper: dict, topic: dict, related_notes: list[str] | None = None
) -> tuple[dict, str]:
    """Run the topic's Structural Skim skill against a paper via Sonnet.

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
    pdf_path = (paper.get("pdf_local_path") or "").strip().strip("'\"")
    is_pdf = False
    use_native_pdf = False  # send PDF bytes directly to Anthropic API
    paper_content = ""

    if pdf_path and not pdf_path.startswith("http"):
        paper_content, is_pdf = extract_skim_sections(pdf_path)
        if not paper_content:
            # Scanned/image PDF — pypdf extracted no text; flag for native API upload
            use_native_pdf = Path(pdf_path).exists()

    if not paper_content and not use_native_pdf:
        # Fallback: abstract only
        abstract = (paper.get("abstract") or "").strip()
        paper_content = abstract[:2000] if abstract else "(no abstract available)"
    elif use_native_pdf:
        paper_content = "(see attached PDF document)"

    if use_native_pdf:
        content_source = "PDF (full document, image/scanned)"
    elif is_pdf:
        content_source = "PDF: abstract + introduction + conclusion + captions"
    else:
        content_source = "abstract only — no PDF available"

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

    # Escape any literal braces in skill/notes so str.format() doesn't choke on them
    safe_skill = skim_skill_md.replace("{", "{{").replace("}", "}}")
    safe_notes = notes_section.replace("{", "{{").replace("}", "}}")
    prompt = _PROMPT_WRAPPER.format(
        skim_skill_md=safe_skill,
        title=title,
        authors=authors_str,
        year=year or "n.d.",
        venue=venue or "—",
        paper_content=paper_content,
        content_source=content_source,
        topic_name=topic_name,
        problems=problems_str,
        keywords=kw_str,
        notes_section=safe_notes,
    )

    from poneglyph.config import settings as _settings
    client = get_client()
    if client is None:
        return {}, "ANTHROPIC_API_KEY is not configured — add it to your .env file"

    user_content: list = []
    if use_native_pdf:
        pdf_bytes = Path(pdf_path).read_bytes()
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        user_content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
        })
        is_pdf = True
    user_content.append({"type": "text", "text": prompt})

    try:
        message = await client.messages.create(
            model=_settings.sonnet_model,
            max_tokens=2048,
            tools=[_SKIM_TOOL],
            tool_choice={"type": "tool", "name": "record_skim"},
            messages=[{"role": "user", "content": user_content}],
        )
        logger.info(
            "synthesize_paper: input_tokens=%d output_tokens=%d pdf=%s",
            message.usage.input_tokens,
            message.usage.output_tokens,
            is_pdf,
        )
    except Exception as exc:
        logger.warning("synthesize_paper API call failed: %s", exc)
        return {}, f"API call failed: {exc}"

    tool_block = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_block is None:
        logger.warning("synthesize_paper: no tool_use block in response")
        return {}, "Model did not return structured output — try regenerating"

    data: dict = tool_block.input if isinstance(tool_block.input, dict) else {}

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

    if rec == "skip" and not result.get("skip_reason"):
        result["skip_reason"] = "(no reason recorded)"

    return result, ""
