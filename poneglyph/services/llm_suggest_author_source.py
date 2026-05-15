"""LLM-assisted RSS feed URL suggestion for authors/publications."""

import json
import logging
import re

from poneglyph.services.llm import call_haiku

logger = logging.getLogger(__name__)

# Well-known sources hardcoded to avoid hallucination
_KNOWN: dict[str, tuple[str, str]] = {
    "quantocracy":        ("https://quantocracy.com/feed/",                    "aggregator"),
    "alpha architect":    ("https://alphaarchitect.com/feed/",                 "aggregator"),
    "fed guy":            ("https://www.fedguy.com/rss/",                      "author"),
    "cheap convexity":    ("https://cheapconvexity.substack.com/feed",         "author"),
    "macro compass":      ("https://themacrocompass.substack.com/feed",        "author"),
    "volatility things":  ("https://volatilitythings.substack.com/feed",       "author"),
    "the diff":           ("https://www.thediff.co/rss/",                      "author"),
    "epsilon theory":     ("https://www.epsilontheory.com/feed/",               "author"),
    "newfound research":  ("https://www.newfoundresearch.com/feed/",            "author"),
    "verdad":             ("https://verdadcap.com/research/feed/",              "aggregator"),
    "epchan":             ("http://epchan.blogspot.com/feeds/posts/default",   "author"),
    "qoppac":             ("http://qoppac.blogspot.com/feeds/posts/default",   "author"),
    "robot wealth":       ("https://robotwealth.com/feed/",                    "author"),
    "musings on markets": ("https://aswathdamodaran.blogspot.com/feeds/posts/default", "author"),
}


def _match_known(name: str) -> tuple[str, str] | None:
    key = name.lower().strip()
    for pattern, (url, entity_type) in _KNOWN.items():
        if pattern in key or key in pattern:
            return url, entity_type
    return None


def _guess_entity_type(name: str) -> str:
    signals = {"quantocracy", "aggregat", "roundup", "digest", "weekly links", "around the web"}
    lname = name.lower()
    return "aggregator" if any(s in lname for s in signals) else "author"


async def suggest_source_url(name: str, hint: str = "") -> tuple[str, str, str]:
    """Suggest an RSS feed URL for an author or publication.

    Returns (candidate_url, entity_type, reason).
    Checks hardcoded known sources first; falls back to Haiku.
    entity_type is 'author', 'aggregator', or 'stub'.
    """
    known = _match_known(name)
    if known:
        url, entity_type = known
        return url, entity_type, "known source (hardcoded)"

    # Substack heuristic
    if "substack" in hint.lower() or "substack" in name.lower():
        slug = re.sub(r"[^a-z0-9]", "", name.lower())[:30]
        return f"https://{slug}.substack.com/feed", "author", "Substack heuristic"

    prompt = f"""\
Suggest the RSS feed URL for this financial research author or publication.

Name: {name}
Hint/platform: {hint or "unknown"}

Prefer these patterns:
- Substack: {{slug}}.substack.com/feed
- WordPress: {{domain}}/feed
- Ghost: {{domain}}/rss
- Blogspot: {{slug}}.blogspot.com/feeds/posts/default

Return ONLY JSON, no commentary:
{{"url": "https://...", "entity_type": "author or aggregator", "reason": "one sentence"}}"""

    text, err = await call_haiku(prompt, max_tokens=200)
    if err or not text:
        return "", _guess_entity_type(name), f"LLM failed: {err}"

    try:
        clean = re.sub(r"```(?:json)?|```", "", text).strip()
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end > start:
            data = json.loads(clean[start:end + 1])
            url = str(data.get("url") or "").strip()
            entity_type = str(data.get("entity_type") or "author").strip()
            if entity_type not in ("author", "aggregator"):
                entity_type = _guess_entity_type(name)
            reason = str(data.get("reason") or "LLM suggestion").strip()
            return url, entity_type, reason
    except Exception as exc:
        logger.warning("suggest_source_url: JSON parse failed: %s", exc)

    return "", _guess_entity_type(name), "could not parse LLM response"
