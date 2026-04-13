"""Fetch paper metadata from the CrossRef API given a DOI URL."""

import re
import xml.etree.ElementTree as ET

import httpx

_CROSSREF_API = "https://api.crossref.org/works/"
_USER_AGENT = "poneglyph/0.1 (mailto:research@example.com)"

# Match doi.org or dx.doi.org URLs, or bare DOIs starting with 10.
_DOI_URL_PATTERN = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE
)


def is_doi_url(url: str) -> bool:
    return bool(_DOI_URL_PATTERN.search(url.strip()))


def extract_doi(url: str) -> str | None:
    m = _DOI_URL_PATTERN.search(url.strip())
    return m.group(1) if m else None


def _strip_jats(text: str) -> str:
    """Strip JATS XML tags from CrossRef abstract strings."""
    try:
        # Wrap in a root element so ET can parse fragments
        root = ET.fromstring(f"<r>{text}</r>")
        return " ".join(root.itertext()).strip()
    except ET.ParseError:
        return re.sub(r"<[^>]+>", " ", text).strip()


def _parse_date(date_obj: dict) -> str:
    """Parse CrossRef date-parts [[year, month, day]] to YYYY-MM-DD or YYYY."""
    parts = date_obj.get("date-parts", [[]])[0]
    if not parts:
        return ""
    if len(parts) >= 3:
        return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
    if len(parts) == 2:
        return f"{parts[0]:04d}-{parts[1]:02d}"
    return str(parts[0])


async def search_by_title(title: str) -> dict | None:
    """Search CrossRef by title string, return metadata for the best match."""
    title = title.strip()
    if not title:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.crossref.org/works",
                params={"query.bibliographic": title, "rows": "1"},
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    items = data.get("message", {}).get("items", [])
    if not items:
        return None
    doi = items[0].get("DOI", "")
    return await fetch_crossref_metadata(doi) if doi else None


async def fetch_crossref_metadata(doi: str) -> dict | None:
    """Fetch metadata for a paper from the CrossRef API.

    Returns dict with keys: title, authors, abstract, published_date, published_venue, url
    or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                _CROSSREF_API + doi,
                headers={"User-Agent": _USER_AGENT},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    msg = data.get("message", {})
    if not msg:
        return None

    # Title
    titles = msg.get("title", [])
    title = titles[0].strip() if titles else ""
    if not title:
        return None

    # Authors — prefer family + given, fall back to name
    authors = []
    for a in msg.get("author", []):
        family = a.get("family", "").strip()
        given = a.get("given", "").strip()
        if family and given:
            authors.append(f"{given} {family}")
        elif family:
            authors.append(family)
        elif a.get("name"):
            authors.append(a["name"].strip())

    # Abstract (often JATS XML)
    abstract_raw = msg.get("abstract", "")
    abstract = _strip_jats(abstract_raw) if abstract_raw else ""

    # Published date — prefer print over online
    date_obj = msg.get("published-print") or msg.get("published-online") or msg.get("published")
    published_date = _parse_date(date_obj) if date_obj else ""

    # Venue / journal
    container = msg.get("container-title", [])
    published_venue = container[0].strip() if container else ""

    # Canonical URL
    url = f"https://doi.org/{doi}"

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "published_date": published_date,
        "published_venue": published_venue,
        "url": url,
        "source": "doi",
        "source_id": doi,
    }
