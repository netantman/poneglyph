"""Article relevance gate — fast Haiku call to decide if an RSS item belongs in a topic."""

import json
import logging
import re
from dataclasses import dataclass

from poneglyph.services.llm import call_haiku

logger = logging.getLogger(__name__)

_THRESHOLD_INGEST = 0.5
_THRESHOLD_BORDERLINE = 0.3


@dataclass
class RelevanceResult:
    relevant: bool     # True if score >= _THRESHOLD_INGEST
    borderline: bool   # True if _THRESHOLD_BORDERLINE <= score < _THRESHOLD_INGEST
    score: float
    reason: str


async def is_relevant(topic: dict, title: str, summary: str) -> RelevanceResult:
    """Gate an article against a topic's keywords and problem statements via Haiku.

    Score >= 0.5  → ingest + synthesize
    0.3 <= score < 0.5 → ingest without synthesis (review queue)
    score < 0.3   → skip
    """
    keywords = (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])
    kw_str = ", ".join(keywords[:20]) or "(none)"
    problems = topic.get("problem_statements") or []
    problems_str = "\n".join(f"- {p}" for p in problems[:5]) or "- (none specified)"

    prompt = f"""\
You are a research assistant screening an article for relevance to a topic.

## Topic: {topic.get("name", "")}
Keywords: {kw_str}
Problem statements:
{problems_str}

## Article
Title: {title}
Preview: {summary[:800] if summary else "(no preview available)"}

Rate relevance 0.0–1.0. Return ONLY JSON, no commentary:
{{"relevant": true/false, "score": 0.0, "reason": "one sentence"}}

Score guide:
0.8-1.0 = directly addresses keywords/problems
0.5-0.79 = useful context
0.3-0.49 = loosely related
0.0-0.29 = not relevant"""

    text, err = await call_haiku(prompt, max_tokens=128)
    if err or not text:
        logger.warning("article_relevance: Haiku failed (%s) — defaulting relevant", err)
        return RelevanceResult(relevant=True, borderline=False, score=0.6, reason="check failed, defaulting")

    try:
        clean = re.sub(r"```(?:json)?|```", "", text).strip()
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end > start:
            data = json.loads(clean[start:end + 1])
            score = float(data.get("score", 0.5))
            score = max(0.0, min(1.0, score))
            relevant = score >= _THRESHOLD_INGEST
            borderline = _THRESHOLD_BORDERLINE <= score < _THRESHOLD_INGEST
            reason = str(data.get("reason", ""))
            return RelevanceResult(relevant=relevant, borderline=borderline, score=score, reason=reason)
    except Exception as exc:
        logger.warning("article_relevance: JSON parse failed: %s — raw: %.200s", exc, text)

    return RelevanceResult(relevant=True, borderline=False, score=0.5, reason="parse failed, defaulting")
