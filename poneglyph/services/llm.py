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


async def call_sonnet(prompt: str, max_tokens: int = 4096) -> str:
    """Send a prompt to the configured Sonnet model and return the text response.

    Returns empty string on any failure — no exceptions propagated to caller.
    """
    client = _get_client()
    if client is None:
        return ""
    try:
        message = await client.messages.create(
            model=settings.sonnet_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = message.usage
        logger.info(
            "call_sonnet: input_tokens=%d output_tokens=%d",
            usage.input_tokens,
            usage.output_tokens,
        )
        return message.content[0].text if message.content else ""
    except Exception as exc:
        logger.warning("call_sonnet failed: %s", exc)
        return ""


async def call_haiku(prompt: str, max_tokens: int = 512) -> tuple[str, str]:
    """Send a prompt to claude-haiku-4-5 and return (text, error).

    On success: (response_text, "").
    On failure: ("", human-readable error message).
    Never raises — errors are returned as the second element.
    """
    client = _get_client()
    if client is None:
        return "", "ANTHROPIC_API_KEY is not configured — add it to your .env file"
    try:
        message = await client.messages.create(
            model=settings.haiku_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = message.usage
        logger.info(
            "call_haiku: input_tokens=%d output_tokens=%d",
            usage.input_tokens,
            usage.output_tokens,
        )
        return (message.content[0].text if message.content else ""), ""
    except anthropic.AuthenticationError:
        msg = "API key is invalid or has been revoked — check ANTHROPIC_API_KEY in .env"
        logger.warning("call_haiku: %s", msg)
        return "", msg
    except anthropic.RateLimitError as exc:
        msg = f"Anthropic rate limit reached — wait a moment and try again ({exc})"
        logger.warning("call_haiku: %s", msg)
        return "", msg
    except anthropic.APIConnectionError as exc:
        msg = f"Could not connect to Anthropic API — check your internet connection ({exc})"
        logger.warning("call_haiku: %s", msg)
        return "", msg
    except Exception as exc:
        msg = f"Haiku API call failed: {exc}"
        logger.warning("call_haiku: %s", msg)
        return "", msg
