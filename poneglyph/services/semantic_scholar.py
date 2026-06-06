"""Semantic Scholar Graph API client with rate limiting."""

import asyncio
import logging
import time

import httpx

from poneglyph.config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.semanticscholar.org/graph/v1"

_PAPER_FIELDS = (
    "paperId,title,authors,abstract,year,venue,"
    "externalIds,citationCount,openAccessPdf"
)
_CITATION_FIELDS = "paperId,title,authors,abstract,year,venue,externalIds,citationCount"

# Rate limiting — conservative: 1 request per second regardless of API key
_rate_lock: asyncio.Lock | None = None
_last_call: float = 0.0
_MIN_INTERVAL: float = 1.1  # seconds between requests
_MAX_RETRIES: int = 5  # retries on 429 / transient errors before giving up
_BACKOFF_SCHEDULE: tuple[int, ...] = (5, 15, 30, 60, 120)  # seconds, indexed by attempt


class S2RateLimitError(Exception):
    """Raised when Semantic Scholar keeps returning 429 after all retries."""


def _lock() -> asyncio.Lock:
    global _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


def _s2_key(identifier: str) -> str:
    """Convert arXiv ID, DOI, URL, or bare S2 ID to the S2 lookup key format."""
    s = identifier.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return f"URL:{s}"
    if s.startswith("arXiv:") or s.startswith("ARXIV:") or s.startswith("DOI:"):
        return s
    if s.startswith("10.") or ("/" in s and not s.startswith("arXiv")):
        return f"DOI:{s}"
    if len(s) == 40 and all(c in "0123456789abcdef" for c in s.lower()):
        return s  # bare S2 hex ID
    return f"arXiv:{s}"


async def _request_once(path: str, params: dict | None) -> httpx.Response | None:
    """Issue a single rate-limited GET. Returns the response or None on connection error."""
    global _last_call
    async with _lock():
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()

        headers: dict[str, str] = {"User-Agent": "poneglyph/0.1 (research tool)"}
        if settings.semantic_scholar_api_key:
            headers["x-api-key"] = settings.semantic_scholar_api_key

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                return await client.get(f"{_API_BASE}{path}", params=params, headers=headers)
        except Exception as exc:
            logger.warning("S2 request error %s: %s", path, exc)
            return None


def _backoff_seconds(resp: httpx.Response | None, attempt: int) -> float:
    """Honor Retry-After header if present, else use the exponential schedule."""
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    idx = min(attempt, len(_BACKOFF_SCHEDULE) - 1)
    return _BACKOFF_SCHEDULE[idx]


async def _get(path: str, params: dict | None = None) -> dict | None:
    """GET with bounded retry on 429 / 5xx / connection errors.

    Raises S2RateLimitError if rate-limited past all retries (so callers can
    distinguish throttling from a genuine empty/missing result, which is None).
    """
    for attempt in range(_MAX_RETRIES + 1):
        resp = await _request_once(path, params)

        # Connection-level failure
        if resp is None:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_backoff_seconds(None, attempt))
                continue
            return None

        if resp.status_code == 404:
            return None

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < _MAX_RETRIES:
                delay = _backoff_seconds(resp, attempt)
                logger.warning(
                    "S2 %s on %s — retry %d/%d after %.0fs",
                    resp.status_code, path, attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue
            if resp.status_code == 429:
                raise S2RateLimitError(f"Semantic Scholar rate limited on {path}")
            logger.warning("S2 %s on %s — giving up", resp.status_code, path)
            return None

        try:
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("S2 bad response %s %s: %s", resp.status_code, path, exc)
            return None

    return None


async def get_paper(identifier: str) -> dict | None:
    """Fetch paper metadata by any identifier (arXiv ID, DOI, URL, or S2 ID)."""
    return await _get(f"/paper/{_s2_key(identifier)}", {"fields": _PAPER_FIELDS})


async def resolve_to_s2_id(identifier: str) -> str | None:
    """Resolve any identifier to a bare Semantic Scholar paper ID."""
    data = await get_paper(identifier)
    return data.get("paperId") if data else None


async def search_paper(title: str, limit: int = 3) -> dict | None:
    """Search for a paper by title. Returns the top result or None."""
    data = await _get("/paper/search", {"query": title, "fields": _PAPER_FIELDS, "limit": limit})
    if not data:
        return None
    results = data.get("data") or []
    return results[0] if results else None


async def search_papers(query: str, limit: int = 10) -> list[dict]:
    """Free-text search returning up to `limit` results."""
    data = await _get("/paper/search", {"query": query, "fields": _PAPER_FIELDS, "limit": min(limit, 100)})
    if not data:
        return []
    return [r for r in (data.get("data") or []) if r.get("paperId") and r.get("title")]


async def get_citations(s2_id: str, limit: int = 100) -> list[dict]:
    """Papers that CITE this paper."""
    return await _fetch_paged(f"/paper/{s2_id}/citations", "citingPaper", limit)


async def get_references(s2_id: str, limit: int = 100) -> list[dict]:
    """Papers that this paper CITES."""
    return await _fetch_paged(f"/paper/{s2_id}/references", "citedPaper", limit)


async def _fetch_paged(path: str, item_key: str, limit: int) -> list[dict]:
    results: list[dict] = []
    offset = 0
    page = min(limit, 100)
    while len(results) < limit:
        data = await _get(path, {"fields": _CITATION_FIELDS, "limit": page, "offset": offset})
        if not data:
            break
        batch = data.get("data") or []
        for item in batch:
            paper = item.get(item_key)
            if paper and paper.get("paperId") and paper.get("title"):
                results.append(paper)
        if not batch or not data.get("next"):
            break
        offset += len(batch)
    return results[:limit]
