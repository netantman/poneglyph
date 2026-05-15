"""Sonnet cross-paper synthesis for a topic.

Uses the bundled multi-paper synthesis skill from claude_requirements/.
"""

import json
import logging
import re
from pathlib import Path

from poneglyph.services.llm import call_sonnet
from poneglyph.services.llm_bulk import strip_html

logger = logging.getLogger(__name__)

_SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "claude_requirements"
    / "skill_multi_paper_synthesis.md"
)


def _load_bundled_skill() -> str:
    if _SKILL_PATH.exists():
        return _SKILL_PATH.read_text(encoding="utf-8")
    logger.warning("llm_cross: bundled skill not found at %s", _SKILL_PATH)
    return ""


_PROMPT = """\
{skill}

---

## Research Topic: {topic_name}
{description}

Problem statements:
{problems}

Keywords: {keywords}

---

## Corpus: {paper_count} source(s)

Evidence tiers used in this corpus:
- **peer_reviewed**: peer-reviewed journal or conference paper
- **working_paper**: arXiv / SSRN pre-print
- **practitioner_blog**: named practitioner blog or newsletter
- **aggregator_pointer**: link via aggregator (Quantocracy etc.)

Each source is tagged [evidence_tier: ...] below.

{paper_sections}

---

Using the synthesis notes and annotations above, produce:

1. A **multi-paper synthesis narrative** that addresses:
   - What progress has been made toward solving the problem statements?
   - What gaps and open questions remain?
   - Where do sources agree, and where do they diverge?
   - Which sources carry the strongest evidence?
   - Weight peer-reviewed and working papers above practitioner blogs for consensus claims.
     Surface practitioner-blog insights separately under a **Practitioner perspective** subsection
     when they extend or contradict the academic consensus — do not let a single high-volume
     blogger dominate consensus over multiple papers.

2. A list of **3–7 concrete research directions** (specific, actionable next steps).

Return ONLY a JSON object — no markdown fences, no commentary:
{{
  "synthesis": "full narrative in Markdown",
  "research_directions": ["direction 1", "direction 2", "..."]
}}
"""


_ACADEMIC_VENUES = {
    "journal of finance", "review of financial studies", "journal of financial economics",
    "review of finance", "management science", "journal of portfolio management",
    "financial analysts journal", "journal of financial and quantitative analysis",
    "nber", "ssrn",
}


def _evidence_tier(paper: dict) -> str:
    """Classify a paper/article into an evidence tier for cross-synthesis weighting."""
    content_type = (paper.get("content_type") or "academic").lower()
    venue = (paper.get("published_venue") or "").lower()
    source = (paper.get("source") or "").lower()

    if content_type == "article":
        return "practitioner_blog"

    # Academic paper — distinguish peer-reviewed from preprint
    if source in ("arxiv",) or "arxiv" in venue:
        return "working_paper"
    if any(v in venue for v in _ACADEMIC_VENUES):
        return "peer_reviewed"
    if venue:
        return "peer_reviewed"
    return "working_paper"


async def cross_synthesize(
    topic: dict,
    paper_notes: list[dict],
) -> tuple[str, list[str]]:
    """Run cross-paper synthesis for a topic via Sonnet.

    Args:
        topic: DB topic row dict.
        paper_notes: list of dicts, each with keys:
            - ``paper``: papers row dict
            - ``skim``: topic_paper_notes row dict (may be None)
            - ``human_note``: str from paper_notes.human_note (may be None)

    Returns:
        ``(synthesis_markdown, research_directions_list)``

    Raises:
        ValueError: if the LLM returns an empty response.
    """
    skill = _load_bundled_skill()
    topic_name = topic.get("name") or ""
    description = topic.get("description") or ""
    problems = topic.get("problem_statements") or []
    all_kw = (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])

    problems_str = "\n".join(f"- {p}" for p in problems[:5]) or "- (none specified)"
    kw_str = ", ".join(all_kw[:20]) or "(none)"

    sections: list[str] = []
    for i, pn in enumerate(paper_notes, 1):
        paper = pn.get("paper") or {}
        skim = pn.get("skim") or {}
        human_note = pn.get("human_note") or ""

        title = paper.get("title") or f"Paper {i}"
        year = (paper.get("published_date") or "")[:4]
        venue = paper.get("published_venue") or ""
        authors = paper.get("authors") or []
        authors_str = ", ".join(authors[:4]) if isinstance(authors, list) else str(authors)
        paper_id = paper.get("id", "")

        tier = _evidence_tier(paper)
        sec = f"### [{i}] {title}\n"
        sec += f"Authors: {authors_str} ({year or 'n.d.'}) | {venue or '—'}\n"
        sec += f"Paper ID: {paper_id} | [evidence_tier: {tier}]\n\n"

        if skim:
            if skim.get("main_claim"):
                sec += f"**Main claim:** {skim['main_claim']}\n"
            if skim.get("signal_mechanism"):
                sec += f"**Signal / mechanism:** {skim['signal_mechanism']}\n"
            if skim.get("headline_statistic"):
                sec += f"**Key finding:** {skim['headline_statistic']}\n"
            if skim.get("sample"):
                sec += f"**Sample:** {skim['sample']}\n"
            if skim.get("deep_synthesis"):
                sec += f"\n**Deep synthesis (excerpt):**\n{skim['deep_synthesis'][:1500]}\n"

        if human_note:
            clean = strip_html(human_note)[:500]
            if clean:
                sec += f"\n**User annotation:** {clean}\n"

        sections.append(sec)

    paper_sections = "\n---\n".join(sections) if sections else "(no papers with synthesis notes)"

    prompt = _PROMPT.format(
        skill=skill,
        topic_name=topic_name,
        description=description,
        problems=problems_str,
        keywords=kw_str,
        paper_count=len(paper_notes),
        paper_sections=paper_sections,
    )

    result = await call_sonnet(prompt, max_tokens=4096)
    if not result:
        raise ValueError("LLM returned empty response — check API key and model availability.")

    # Parse JSON envelope
    clean = re.sub(r"```(?:json)?|```", "", result).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1 or end <= start:
        # Model returned plain text instead of JSON — accept synthesis as-is
        logger.warning("llm_cross: response was not JSON; using raw output as synthesis")
        return result, []

    try:
        data = json.loads(clean[start : end + 1])
        synthesis = str(data.get("synthesis") or "").strip()
        raw_dirs = data.get("research_directions") or []
        directions = [str(d) for d in raw_dirs if d][:10]
        return synthesis or result, directions
    except json.JSONDecodeError as exc:
        logger.warning("llm_cross JSON parse failed: %s", exc)
        return result, []
