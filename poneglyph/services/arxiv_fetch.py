"""Fetch paper metadata from the arXiv API given a URL or paper ID."""

import re
import xml.etree.ElementTree as ET

import httpx

# arXiv Atom namespace
_NS = {"atom": "http://www.w3.org/2005/Atom"}
_ARXIV_API = "http://export.arxiv.org/api/query"

# Patterns to extract arXiv ID from URLs
_ARXIV_PATTERNS = [
    re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)"),
    re.compile(r"arxiv\.org/pdf/(\d{4}\.\d{4,5}(?:v\d+)?)"),
    re.compile(r"arxiv\.org/abs/([a-z\-]+/\d{7}(?:v\d+)?)"),
    re.compile(r"arxiv\.org/pdf/([a-z\-]+/\d{7}(?:v\d+)?)"),
]


def is_arxiv_url(url: str) -> bool:
    """Check if a URL is an arXiv paper link."""
    return any(p.search(url) for p in _ARXIV_PATTERNS)


def extract_arxiv_id(url: str) -> str | None:
    """Extract arXiv paper ID from a URL."""
    for pattern in _ARXIV_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


async def fetch_arxiv_metadata(arxiv_id: str) -> dict | None:
    """Fetch metadata for a paper from the arXiv API.

    Returns dict with keys: title, authors, abstract, published_date, pdf_url, url
    or None on failure.
    """
    params = {"id_list": arxiv_id, "max_results": "1"}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(_ARXIV_API, params=params)
            resp.raise_for_status()
    except httpx.HTTPError:
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None

    entry = root.find("atom:entry", _NS)
    if entry is None:
        return None

    # Check for error (arXiv returns an entry with id but no title for invalid IDs)
    title_el = entry.find("atom:title", _NS)
    if title_el is None or not title_el.text or title_el.text.strip() == "Error":
        return None

    title = " ".join(title_el.text.split())

    authors = []
    for author_el in entry.findall("atom:author", _NS):
        name_el = author_el.find("atom:name", _NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    abstract_el = entry.find("atom:summary", _NS)
    abstract = " ".join(abstract_el.text.split()) if abstract_el is not None and abstract_el.text else ""

    # Use last revised date (atom:updated), falling back to first submitted (atom:published)
    updated_el = entry.find("atom:updated", _NS)
    published_el = entry.find("atom:published", _NS)
    date_el = updated_el if updated_el is not None and updated_el.text else published_el
    published_date = ""
    if date_el is not None and date_el.text:
        published_date = date_el.text[:10]  # YYYY-MM-DD

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    paper_url = f"https://arxiv.org/abs/{arxiv_id}"

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "published_date": published_date,
        "pdf_url": pdf_url,
        "url": paper_url,
        "source": "arxiv",
        "source_id": arxiv_id,
    }
