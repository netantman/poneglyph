"""Haiku-based steering suggestions from aggregated human notes.

Analyses the human notes the user has written for papers in a topic and
suggests keyword/problem-statement adjustments.  Intentionally cheap —
Haiku-only, no PDFs, low token budget.
"""

from __future__ import annotations

import json
import logging
import re

from poneglyph.services.llm import call_haiku

logger = logging.getLogger(__name__)

_PROMPT_TMPL = """\
You are a research assistant helping a user steer their literature-review \
topic based on notes they have written while reading papers.

## Current topic configuration

**Topic:** {topic_name}

**Current keywords:**
{keywords}

**Current problem statements:**
{problem_statements}

## Human notes on papers in this topic

{notes_block}

---

Based only on the notes above, suggest improvements to the keyword list and \
problem statements.  Rules:
- Suggest ADDING a keyword only if it appears repeatedly in the notes and is \
  not already present.
- Suggest REMOVING a keyword only if the notes consistently show it is off-topic.
- Suggest ADDING a problem statement only if multiple notes reveal an important \
  research question the user is chasing that is not yet captured.
- Suggest REMOVING a problem statement only if none of the notes address it at \
  all — it may be stale.
- If the notes are sparse or uninformative, return empty lists rather than \
  guessing.
- Keep every suggestion concise (≤ 10 words for keywords, ≤ 80 words for \
  problem statements).

Return **only** a JSON object with this exact structure, no other text:
{{
  "keywords_add": ["term1", "term2"],
  "keywords_remove": ["old_term"],
  "ps_add": ["New problem statement."],
  "ps_remove": ["Existing problem statement to remove verbatim."],
  "reasoning": "One or two sentences explaining the main patterns you spotted."
}}
"""

_EMPTY: dict = {
    "keywords_add": [],
    "keywords_remove": [],
    "ps_add": [],
    "ps_remove": [],
    "reasoning": "",
}


def _parse_response(raw: str) -> dict:
    """Extract JSON from the LLM response; return _EMPTY on any parse error."""
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    text = match.group(1).strip() if match else raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("llm_suggest: failed to parse JSON response: %r", raw[:300])
        return _EMPTY

    result: dict = {**_EMPTY}
    for key in ("keywords_add", "keywords_remove", "ps_add", "ps_remove"):
        val = data.get(key, [])
        result[key] = [str(v).strip() for v in val if str(v).strip()] if isinstance(val, list) else []
    result["reasoning"] = str(data.get("reasoning", "")).strip()
    return result


async def suggest_steering(topic: dict, notes: list[dict]) -> tuple[dict, str]:
    """Generate keyword/PS suggestions from the topic's human notes.

    Args:
        topic: topic row dict (name, keywords, problem_statements, …)
        notes: list of dicts with keys ``title`` and ``note`` (both strings)

    Returns:
        (suggestions_dict, error_message)
        suggestions_dict has keys: keywords_add, keywords_remove, ps_add,
        ps_remove, reasoning — all lists/strings, never None.
        error_message is "" on success.
    """
    if not notes:
        return _EMPTY, "No human notes found for papers in this topic — write some notes first."

    notes_with_content = [n for n in notes if (n.get("note") or "").strip()]
    if not notes_with_content:
        return _EMPTY, "Human notes exist but are all empty."

    keywords = topic.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    ps = topic.get("problem_statements") or []
    if not isinstance(ps, list):
        ps = []

    notes_block = "\n\n".join(
        f"**{n['title']}**\n{n['note'].strip()}" for n in notes_with_content[:20]
    )

    prompt = _PROMPT_TMPL.format(
        topic_name=topic.get("name", ""),
        keywords=", ".join(keywords) if keywords else "(none)",
        problem_statements="\n".join(f"- {p}" for p in ps) if ps else "(none)",
        notes_block=notes_block,
    )

    text, error = await call_haiku(prompt, max_tokens=800)
    if error:
        return _EMPTY, error
    if not text:
        return _EMPTY, "LLM returned an empty response."

    suggestions = _parse_response(text)

    # Filter out suggestions that are already in / not in the current lists
    existing_kw = {k.lower() for k in keywords}
    suggestions["keywords_add"] = [k for k in suggestions["keywords_add"] if k.lower() not in existing_kw]
    suggestions["keywords_remove"] = [k for k in suggestions["keywords_remove"] if k.lower() in existing_kw]

    return suggestions, ""
