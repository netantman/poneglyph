"""Haiku article synthesis — run the topic's Article Skim skill against a blog post."""

import hashlib
import json
import logging

from poneglyph.services.llm import get_client
from poneglyph.services.llm_bulk import strip_html

logger = logging.getLogger(__name__)

_VALID_RECS = {"read", "skip", "deep_dive"}
_MAX_BODY_CHARS = 8_000

_PROMPT_WRAPPER = """\
{article_skill_md}

---

## Article
Title: {title}
Author: {author}
Published: {published_date}
Publication: {venue}
URL: {url}

## Content
{body_text}

## Research Topic: {topic_name}
Problem statements:
{problems}

Keywords: {keywords}

## Recent notes from this topic
{notes_section}

Use the record_article_skim tool to record your findings.
For any field you cannot determine, use an empty string (or empty list for cross_references).
"""

_ARTICLE_SKIM_TOOL = {
    "name": "record_article_skim",
    "description": "Record the article skim results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thesis": {
                "type": "string",
                "description": "One-sentence claim the author is making",
            },
            "mechanism": {
                "type": "string",
                "description": "Causal story or mechanism the author asserts",
            },
            "evidence_type": {
                "type": "string",
                "enum": ["anecdote", "market_data_illustration", "cited_paper",
                         "personal_experience", "assertion_only", "mixed"],
            },
            "concrete_numbers": {
                "type": "string",
                "description": "Any specific numbers cited (returns, Sharpe, etc.) — empty if none",
            },
            "author_stance": {
                "type": "string",
                "description": "Credibility signals — practitioner, commentator, named desk experience",
            },
            "cross_references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Papers or articles the author links to or cites",
            },
            "actionability": {
                "type": "string",
                "description": "What a reader would do differently after reading",
            },
            "recommendation": {
                "type": "string",
                "enum": ["read", "skip", "save_for_reference"],
            },
        },
        "required": [
            "thesis", "mechanism", "evidence_type", "concrete_numbers",
            "author_stance", "cross_references", "actionability", "recommendation",
        ],
    },
}


def skill_hash(skill_md: str | None) -> str:
    if not skill_md:
        return ""
    return hashlib.sha256(skill_md.encode()).hexdigest()


async def synthesize_article(
    paper: dict,
    topic: dict,
    body_text: str,
    related_notes: list[str] | None = None,
) -> tuple[dict, str]:
    """Run the topic's Article Skim skill against a blog post via Haiku.

    Returns (result_dict, error_str). result_dict maps to topic_paper_notes columns.
    Returns ({}, "") when the topic has no article_skim_skill_md — silent skip.
    """
    article_skill_md = (topic.get("article_skim_skill_md") or "").strip()
    if not article_skill_md:
        logger.warning(
            "synthesize_article: topic '%s' has no article_skim_skill_md — skipping",
            topic.get("name", "?"),
        )
        return {}, ""

    title = paper.get("title") or ""
    url = paper.get("url") or ""
    venue = paper.get("published_venue") or ""
    published_date = (paper.get("published_date") or "")[:10]

    authors = paper.get("authors") or []
    author = ", ".join(authors[:3]) if isinstance(authors, list) else str(authors)

    # Clean and cap body
    clean_body = strip_html(body_text) if "<" in body_text else body_text
    clean_body = clean_body[:_MAX_BODY_CHARS]

    topic_name = topic.get("name") or ""
    all_kw = (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])
    problems = topic.get("problem_statements") or []
    problems_str = "\n".join(f"- {p}" for p in problems[:5]) or "- (none specified)"
    kw_str = ", ".join(all_kw[:20]) or "(none)"

    notes_section = "(none)"
    if related_notes:
        cleaned = [strip_html(n)[:400] for n in related_notes if n][:5]
        cleaned = [c for c in cleaned if c]
        if cleaned:
            notes_section = "\n---\n".join(cleaned)

    safe_skill = article_skill_md.replace("{", "{{").replace("}", "}}")
    safe_notes = notes_section.replace("{", "{{").replace("}", "}}")
    prompt = _PROMPT_WRAPPER.format(
        article_skill_md=safe_skill,
        title=title,
        author=author or "—",
        published_date=published_date or "—",
        venue=venue or "—",
        url=url,
        body_text=clean_body or "(no body text available)",
        topic_name=topic_name,
        problems=problems_str,
        keywords=kw_str,
        notes_section=safe_notes,
    )

    from poneglyph.config import settings as _settings

    client = get_client()
    if client is None:
        return {}, "ANTHROPIC_API_KEY is not configured"

    try:
        message = await client.messages.create(
            model=_settings.haiku_model,
            max_tokens=2048,
            tools=[_ARTICLE_SKIM_TOOL],
            tool_choice={"type": "tool", "name": "record_article_skim"},
            messages=[{"role": "user", "content": prompt}],
        )
        logger.info(
            "synthesize_article: input_tokens=%d output_tokens=%d",
            message.usage.input_tokens,
            message.usage.output_tokens,
        )
    except Exception as exc:
        logger.warning("synthesize_article API call failed: %s", exc)
        return {}, f"API call failed: {exc}"

    tool_block = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_block is None:
        return {}, "Model did not return structured output"

    data: dict = tool_block.input if isinstance(tool_block.input, dict) else {}

    rec = data.get("recommendation", "skip")
    # 'save_for_reference' maps to 'deep_dive' for DB CHECK constraint compatibility
    if rec == "save_for_reference":
        rec = "deep_dive"
    if rec not in _VALID_RECS:
        rec = "skip"

    cross_refs = data.get("cross_references") or []
    if isinstance(cross_refs, str):
        cross_refs = [cross_refs]

    return {
        "skim_recommendation": rec,
        # Map article fields onto topic_paper_notes columns
        "main_claim": str(data.get("thesis") or ""),
        "signal_mechanism": str(data.get("mechanism") or ""),
        "data_source": str(data.get("evidence_type") or ""),
        "headline_statistic": str(data.get("concrete_numbers") or ""),
        "key_metrics": str(data.get("author_stance") or ""),
        "key_tables": cross_refs,
        "portfolio_construction": str(data.get("actionability") or ""),
        # Academic fields unused for articles
        "strategy_type": "",
        "data_details": "",
        "sample": "",
        "universe": "",
    }, ""
