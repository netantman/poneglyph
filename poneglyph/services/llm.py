"""Anthropic LLM helpers — wraps the cheapest model (Haiku) for metadata extraction."""

import base64
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


async def call_sonnet(prompt: str, max_tokens: int = 4096, model: str | None = None) -> str:
    """Send a prompt to a Sonnet-class (or specified) model and return the text response.

    Args:
        prompt: The user prompt.
        max_tokens: Maximum tokens in the response.
        model: Override the model ID. Defaults to ``settings.sonnet_model``.

    Returns empty string on any failure — no exceptions propagated to caller.
    """
    client = _get_client()
    if client is None:
        return ""
    model_id = model or settings.sonnet_model
    try:
        message = await client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = message.usage
        logger.info(
            "call_sonnet: model=%s input_tokens=%d output_tokens=%d",
            model_id,
            usage.input_tokens,
            usage.output_tokens,
        )
        return message.content[0].text if message.content else ""
    except Exception as exc:
        logger.warning("call_sonnet failed (model=%s): %s", model_id, exc)
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


async def call_sonnet_with_pdf(
    prompt: str, pdf_bytes: bytes, max_tokens: int = 4096, model: str | None = None
) -> str:
    """Send a prompt + PDF document to a Sonnet-class model and return the text response.

    Sends the PDF as a native Anthropic document block (handles scanned/image PDFs).
    Returns empty string on any failure.
    """
    client = _get_client()
    if client is None:
        return ""
    model_id = model or settings.sonnet_model
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    try:
        message = await client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        usage = message.usage
        logger.info(
            "call_sonnet_with_pdf: model=%s input_tokens=%d output_tokens=%d",
            model_id,
            usage.input_tokens,
            usage.output_tokens,
        )
        return message.content[0].text if message.content else ""
    except Exception as exc:
        logger.warning("call_sonnet_with_pdf failed (model=%s): %s", model_id, exc)
        return ""


def get_client() -> anthropic.AsyncAnthropic | None:
    """Return the shared AsyncAnthropic client (None if API key is missing)."""
    return _get_client()


async def call_haiku_with_pdf(prompt: str, pdf_bytes: bytes, max_tokens: int = 1024) -> tuple[str, str]:
    """Send a prompt + PDF document to Haiku and return (text, error).

    The PDF is sent as a native Anthropic document block, which supports both
    text-based and scanned/image-based PDFs via Anthropic's built-in processing.
    """
    client = _get_client()
    if client is None:
        return "", "ANTHROPIC_API_KEY is not configured — add it to your .env file"
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    try:
        message = await client.messages.create(
            model=settings.haiku_model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        usage = message.usage
        logger.info(
            "call_haiku_with_pdf: input_tokens=%d output_tokens=%d",
            usage.input_tokens,
            usage.output_tokens,
        )
        return (message.content[0].text if message.content else ""), ""
    except anthropic.AuthenticationError:
        msg = "API key is invalid or has been revoked — check ANTHROPIC_API_KEY in .env"
        logger.warning("call_haiku_with_pdf: %s", msg)
        return "", msg
    except anthropic.RateLimitError as exc:
        msg = f"Anthropic rate limit reached — wait a moment and try again ({exc})"
        logger.warning("call_haiku_with_pdf: %s", msg)
        return "", msg
    except anthropic.APIConnectionError as exc:
        msg = f"Could not connect to Anthropic API — check your internet connection ({exc})"
        logger.warning("call_haiku_with_pdf: %s", msg)
        return "", msg
    except Exception as exc:
        msg = f"Haiku PDF API call failed: {exc}"
        logger.warning("call_haiku_with_pdf: %s", msg)
        return "", msg
