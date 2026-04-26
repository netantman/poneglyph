"""Search route — keyword (FTS5) and semantic (vector) search, plus Q&A."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.templating import templates

router = APIRouter(prefix="/search", tags=["search"])

_HISTORY_LIMIT = 50


def _recent_qa() -> list[dict]:
    rows = fetch_all(
        "SELECT id, question, created_at FROM qa_history ORDER BY created_at DESC LIMIT ?",
        (_HISTORY_LIMIT,),
    )
    return [row_to_dict(r) for r in rows]


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
            "qa_history": _recent_qa(),
        },
    )


@router.post("/ask", response_class=HTMLResponse)
async def ask_question(request: Request, question: str = Form("")):
    """Answer a question about the paper collection using vector + FTS5 + Sonnet."""
    question = question.strip()
    if not question:
        return HTMLResponse(
            '<p style="color:var(--pico-muted-color);">Please enter a question.</p>'
        )

    try:
        from poneglyph.services.llm_qa import answer_question
        answer_md = await answer_question(question)
    except Exception as exc:
        return HTMLResponse(
            f'<p style="color:var(--pico-del-color);">Error: {exc}</p>'
        )

    execute(
        "INSERT INTO qa_history (question, answer_md) VALUES (?, ?)",
        (question, answer_md),
    )

    return templates.TemplateResponse(
        "search/partials/qa_answer.html",
        {"request": request, "question": question, "answer_md": answer_md},
    )


@router.get("/qa/{qa_id}", response_class=HTMLResponse)
async def get_qa(request: Request, qa_id: int):
    """Return a stored Q&A answer as a modal overlay."""
    row = fetch_one("SELECT question, answer_md FROM qa_history WHERE id = ?", (qa_id,))
    if not row:
        return HTMLResponse('<p style="color:var(--pico-del-color);">Not found.</p>')
    return templates.TemplateResponse(
        "search/partials/qa_modal.html",
        {"request": request, "question": row["question"], "answer_md": row["answer_md"]},
    )


@router.delete("/qa/{qa_id}", response_class=HTMLResponse)
async def delete_qa(qa_id: int):
    """Delete a Q&A history entry."""
    execute("DELETE FROM qa_history WHERE id = ?", (qa_id,))
    return HTMLResponse("")
