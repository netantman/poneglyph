"""Search route — keyword (FTS5) and semantic (vector) search."""

import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from poneglyph.db import fetch_all, row_to_dict
from poneglyph.templating import templates

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", mode: str = "keyword"):
    q = q.strip()
    keyword_results: list[dict] = []
    semantic_results: list[dict] = []
    error: str = ""

    if q:
        if mode in ("keyword", "both"):
            try:
                rows = fetch_all(
                    """SELECT p.* FROM papers p
                       JOIN papers_fts fts ON p.id = fts.rowid
                       WHERE papers_fts MATCH ?
                       ORDER BY rank
                       LIMIT 30""",
                    (q,),
                )
                keyword_results = [row_to_dict(r) for r in rows]
            except Exception as exc:
                error = f"Keyword search error: {exc}"

        if mode in ("semantic", "both"):
            try:
                from poneglyph.services.relevance import semantic_search
                semantic_results = semantic_search(q, top_k=20)
            except Exception as exc:
                error = f"Semantic search error: {exc}"

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "q": q,
            "mode": mode,
            "keyword_results": keyword_results,
            "semantic_results": semantic_results,
            "error": error,
        },
    )
