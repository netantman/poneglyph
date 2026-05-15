"""RSS feed fetching and parsing with conditional GET support."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Poneglyph/1.0 research-scout"}

_PAYWALL_RE = re.compile(
    r"(subscribe\s+to\s+(read|continue)|upgrade\s+to\s+paid|paid\s+subscribers?\s+only"
    r"|continue\s+reading|read\s+the\s+full\s+story)",
    re.IGNORECASE,
)


@dataclass
class RssItem:
    guid: str
    title: str
    link: str
    published_dt: Optional[datetime]
    summary: str          # teaser (always present)
    content_html: str     # full HTML body when feed provides it
    author_name: str


@dataclass
class FeedResult:
    items: list[RssItem] = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    not_modified: bool = False
    error: Optional[str] = None


def _parse_published(entry: dict) -> Optional[datetime]:
    for date_field in ("published_parsed", "updated_parsed"):
        t = entry.get(date_field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


async def fetch_feed(
    url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FeedResult:
    """Async-fetch and parse an RSS feed with conditional GET."""
    headers = dict(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        return FeedResult(error=str(exc))

    if resp.status_code == 304:
        return FeedResult(etag=etag, last_modified=last_modified, not_modified=True)

    if resp.status_code != 200:
        return FeedResult(error=f"HTTP {resp.status_code}")

    new_etag = resp.headers.get("ETag")
    new_lm = resp.headers.get("Last-Modified")

    feed = feedparser.parse(resp.content)

    if feed.bozo and not feed.entries:
        exc_str = str(getattr(feed, "bozo_exception", "unknown parse error"))
        return FeedResult(etag=new_etag, last_modified=new_lm, error=f"Parse error: {exc_str}")

    items: list[RssItem] = []
    for entry in feed.entries:
        guid = entry.get("id") or entry.get("link") or ""
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""

        content_html = ""
        if hasattr(entry, "content") and entry.content:
            content_html = entry.content[0].get("value") or ""

        summary = entry.get("summary") or ""

        feed_author = ""
        if hasattr(feed, "feed"):
            feed_author = feed.feed.get("author") or feed.feed.get("title") or ""
        author_name = (entry.get("author") or feed_author or "").strip()

        items.append(RssItem(
            guid=guid,
            title=title,
            link=link,
            published_dt=_parse_published(entry),
            summary=summary,
            content_html=content_html,
            author_name=author_name,
        ))

    return FeedResult(items=items, etag=new_etag, last_modified=new_lm)


async def verify_feed(url: str) -> tuple[bool, str]:
    """Verify a URL returns a parseable RSS feed with at least one item.

    Returns (is_valid, error_message). error_message is "" on success.
    """
    result = await fetch_feed(url)
    if result.error:
        return False, f"Fetch error: {result.error}"
    if result.not_modified or not result.items:
        return False, "Feed parsed but contains no items — check the URL"
    return True, ""


def is_paywalled(item: RssItem) -> bool:
    """Heuristically detect whether an RSS item is behind a paywall."""
    body = item.content_html or item.summary or ""
    body_plain = re.sub(r"<[^>]+>", " ", body)

    if len(body_plain.strip()) < 500:
        tail = body_plain.strip()[-200:]
        if _PAYWALL_RE.search(tail):
            return True
        if len(body_plain.strip()) < 200:
            return True

    if _PAYWALL_RE.search(body_plain[-300:]):
        return True

    if 'class="paywall"' in body or 'id="paywall"' in body:
        return True

    return False


def item_body(item: RssItem) -> str:
    """Return the best available body text for an RSS item."""
    return item.content_html if item.content_html else item.summary
