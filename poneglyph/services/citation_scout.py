"""Citation graph traversal — discover new papers from existing ones via Semantic Scholar."""

import json
import logging

from poneglyph.db import execute, fetch_one, row_to_dict
from poneglyph.services.semantic_scholar import get_citations, get_paper, get_references

logger = logging.getLogger(__name__)


def _lookup_key(paper: dict) -> str | None:
    """Return the best S2 lookup key for a paper already in the DB."""
    if paper.get("semantic_scholar_id"):
        return paper["semantic_scholar_id"]
    if paper.get("source") == "arxiv" and paper.get("source_id"):
        return f"arXiv:{paper['source_id']}"
    if paper.get("source") == "doi" and paper.get("source_id"):
        return f"DOI:{paper['source_id']}"
    if paper.get("url"):
        return paper["url"]
    return None


def _keyword_matches(text: str, keywords: set[str]) -> bool:
    if not keywords:
        return True  # no filter — keep everything
    t = text.lower()
    return any(kw in t for kw in keywords)


def _s2_to_db_fields(s2: dict) -> dict:
    """Map a Semantic Scholar paper dict to DB column values."""
    ext = s2.get("externalIds") or {}

    if ext.get("ArXiv"):
        source = "arxiv"
        source_id = ext["ArXiv"]
        url = f"https://arxiv.org/abs/{ext['ArXiv']}"
    elif ext.get("DOI"):
        source = "doi"
        source_id = ext["DOI"]
        url = f"https://doi.org/{ext['DOI']}"
    else:
        source = "semantic_scholar"
        source_id = s2["paperId"]
        url = f"https://www.semanticscholar.org/paper/{s2['paperId']}"

    authors = [a.get("name", "") for a in (s2.get("authors") or []) if a.get("name")]
    year = s2.get("year")

    return {
        "source": source,
        "source_id": source_id,
        "semantic_scholar_id": s2.get("paperId") or "",
        "title": (s2.get("title") or "").strip(),
        "authors": json.dumps(authors),
        "abstract": (s2.get("abstract") or "").strip(),
        "published_venue": (s2.get("venue") or "").strip(),
        "published_date": str(year) if year else None,
        "url": url,
        "pdf_url": ((s2.get("openAccessPdf") or {}).get("url") or ""),
    }


def _upsert_paper(fields: dict) -> tuple[int, bool]:
    """Insert paper if not present, else return existing id. Returns (paper_id, is_new)."""
    # Prefer matching by S2 ID (most reliable)
    if fields["semantic_scholar_id"]:
        row = fetch_one(
            "SELECT id FROM papers WHERE semantic_scholar_id = ?",
            (fields["semantic_scholar_id"],),
        )
        if row:
            return row["id"], False

    # Match by (source, source_id)
    row = fetch_one(
        "SELECT id FROM papers WHERE source = ? AND source_id = ?",
        (fields["source"], fields["source_id"]),
    )
    if row:
        # Back-fill semantic_scholar_id if missing
        if fields["semantic_scholar_id"]:
            execute(
                "UPDATE papers SET semantic_scholar_id = ? WHERE id = ? "
                "AND (semantic_scholar_id IS NULL OR semantic_scholar_id = '')",
                (fields["semantic_scholar_id"], row["id"]),
            )
        return row["id"], False

    # Match by exact title (case-insensitive) as last resort
    if fields["title"]:
        row = fetch_one(
            "SELECT id FROM papers WHERE LOWER(title) = LOWER(?)", (fields["title"],)
        )
        if row:
            return row["id"], False

    paper_id = execute(
        """INSERT INTO papers
           (source, source_id, semantic_scholar_id, title, authors,
            published_venue, published_date, abstract, url, pdf_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fields["source"], fields["source_id"], fields["semantic_scholar_id"],
            fields["title"], fields["authors"], fields["published_venue"],
            fields["published_date"], fields["abstract"], fields["url"], fields["pdf_url"],
        ),
    )
    execute("INSERT INTO paper_notes (paper_id) VALUES (?)", (paper_id,))
    return paper_id, True


def _link_to_topic(paper_id: int, topic_id: int) -> bool:
    """Link paper to topic if not already linked. Returns True if a new link was created."""
    row = fetch_one(
        "SELECT id FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
        (topic_id, paper_id),
    )
    if not row:
        execute(
            "INSERT INTO topic_papers (topic_id, paper_id) VALUES (?, ?)",
            (topic_id, paper_id),
        )
        return True
    return False


def _record_citation(from_id: int, to_id: int, direction: str) -> None:
    row = fetch_one(
        "SELECT id FROM paper_citations "
        "WHERE from_paper_id = ? AND to_paper_id = ? AND direction = ?",
        (from_id, to_id, direction),
    )
    if not row:
        execute(
            "INSERT INTO paper_citations (from_paper_id, to_paper_id, direction) "
            "VALUES (?, ?, ?)",
            (from_id, to_id, direction),
        )


async def discover_from_paper(
    paper_id: int,
    topic_id: int,
    citations_limit: int = 100,
    references_limit: int = 100,
) -> list[int]:
    """1-hop citation + reference discovery for a single paper.

    Returns IDs of papers newly associated with the topic — both papers that are
    brand-new to the DB and papers already in the DB that weren't yet linked to
    this topic. Papers already associated with the topic are skipped entirely.
    """
    paper = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))
    if not paper:
        return []

    topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
    if not topic:
        return []

    lookup = _lookup_key(paper)
    if not lookup:
        logger.info("discover_from_paper: no S2 lookup key for paper %d", paper_id)
        return []

    s2_data = await get_paper(lookup)
    if not s2_data:
        logger.info("discover_from_paper: S2 not found for paper %d (%s)", paper_id, lookup)
        return []

    s2_id = s2_data.get("paperId", "")
    if s2_id and not paper.get("semantic_scholar_id"):
        execute("UPDATE papers SET semantic_scholar_id = ? WHERE id = ?", (s2_id, paper_id))

    # Build keyword filter — explicit keywords + significant words from problem statements
    keywords: set[str] = {
        kw.lower().strip()
        for kw in (topic.get("keywords") or []) + (topic.get("priority_keywords") or [])
        if kw.strip()
    }
    for ps in (topic.get("problem_statements") or []):
        for word in ps.lower().split():
            word = word.strip(".,!?;:()'\"")
            if len(word) > 4:
                keywords.add(word)

    citations = await get_citations(s2_id, limit=citations_limit)
    references = await get_references(s2_id, limit=references_limit)

    new_ids: list[int] = []
    for s2_paper, direction in (
        [(c, "cited_by") for c in citations] + [(r, "cites") for r in references]
    ):
        if not s2_paper.get("title"):
            continue

        search_text = f"{s2_paper.get('title', '')} {s2_paper.get('abstract', '')}"
        if not _keyword_matches(search_text, keywords):
            continue

        fields = _s2_to_db_fields(s2_paper)
        if not fields["title"]:
            continue

        linked_paper_id, _ = _upsert_paper(fields)
        newly_linked = _link_to_topic(linked_paper_id, topic_id)
        if newly_linked:
            new_ids.append(linked_paper_id)

        _record_citation(paper_id, linked_paper_id, direction)

    logger.info(
        "discover_from_paper: paper=%d topic=%d citations=%d refs=%d new=%d",
        paper_id, topic_id, len(citations), len(references), len(new_ids),
    )
    return new_ids
