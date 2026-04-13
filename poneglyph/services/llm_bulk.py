"""Haiku bulk synthesis — generate structured notes for a paper in context of a topic."""

import json
import logging
import re
from html.parser import HTMLParser

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


# ---------- Synthesis ----------

_VALID_RECS = {"read", "skip", "deep_dive"}

_PROMPT = """\
You are a research analyst at a quantitative hedge fund. Analyze the paper below in context of \
the stated research topic. Be concise and practically oriented.

## Paper
Title: {title}
Authors: {authors} ({year})
Venue: {venue}
Abstract: {abstract}

## Research Topic: {topic_name}
Problem statements:
{problems}

Keywords: {keywords}
{notes_section}
## Task
Reply with ONLY a valid JSON object (no markdown fences, no commentary):
{{
  "key_insights": ["insight 1 (≤15 words)", "insight 2", "insight 3"],
  "trading_applications": "2-3 sentences on concrete trading or portfolio implications",
  "recommendation": "read|skip|deep_dive",
  "recommendation_reason": "one sentence"
}}

Recommendation guide:
- deep_dive: directly solves a stated problem or introduces a critical technique
- read: relevant, worth understanding
- skip: tangential or already well-known"""


async def synthesize_paper(paper: dict, topic: dict, related_notes: list[str] | None = None) -> dict:
    """Call Haiku to generate structured notes for a paper.

    Args:
        paper: DB paper dict (title, authors, abstract, published_date, published_venue, …)
        topic: DB topic dict (name, problem_statements, keywords, priority_keywords)
        related_notes: human_note HTML strings from related papers (stripped before use)

    Returns dict with keys: key_insights (list), trading_applications (str),
    recommendation (str), recommendation_reason (str). Empty dict on failure.
    """
    title = paper.get("title") or ""
    year = (paper.get("published_date") or "")[:4]
    venue = paper.get("published_venue") or ""
    abstract = (paper.get("abstract") or "")[:2000]

    authors = paper.get("authors") or []
    if isinstance(authors, list):
        authors_str = ", ".join(authors[:6])
    else:
        authors_str = str(authors)

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

    prompt = _PROMPT.format(
        title=title,
        authors=authors_str,
        year=year or "n.d.",
        venue=venue or "—",
        abstract=abstract or "(no abstract available)",
        topic_name=topic_name,
        problems=problems_str,
        keywords=kw_str,
        notes_section=notes_section,
    )

    raw = await call_haiku(prompt, max_tokens=512)
    if not raw:
        return {}

    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.warning("synthesize_paper JSON parse failed: %s — raw: %.300s", exc, raw)
        return {}

    if not isinstance(data, dict):
        return {}

    insights = data.get("key_insights") or []
    if isinstance(insights, str):
        insights = [insights]

    rec = data.get("recommendation", "skip")
    if rec not in _VALID_RECS:
        rec = "skip"

    return {
        "key_insights": insights,
        "trading_applications": str(data.get("trading_applications") or ""),
        "recommendation": rec,
        "recommendation_reason": str(data.get("recommendation_reason") or ""),
    }
