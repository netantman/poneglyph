"""Anthropic LLM helpers — wraps the cheapest model (Haiku) for metadata extraction."""

import logging

import anthropic

from poneglyph.config import settings

logger = logging.getLogger(__name__)

_CLIENT: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic | None:
    if not settings.anthropic_api_key:
        logger.warning("call_haiku: ANTHROPIC_API_KEY not configured")
        return None
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _CLIENT


async def call_haiku(prompt: str, max_tokens: int = 512) -> str:
    """Send a prompt to claude-haiku-4-5 and return the text response.

    Returns empty string on any failure — no exceptions propagated to caller.
    """
    client = _get_client()
    if client is None:
        return ""
    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = message.usage
        logger.info(
            "call_haiku: input_tokens=%d output_tokens=%d",
            usage.input_tokens,
            usage.output_tokens,
        )
        return message.content[0].text if message.content else ""
    except Exception as exc:
        logger.warning("call_haiku failed: %s", exc)
        return ""
