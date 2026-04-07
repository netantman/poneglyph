"""Paper list, detail, manual upload, and human note routes."""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from poneglyph.templating import templates
from poneglyph.config import settings
from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.services.arxiv_fetch import extract_arxiv_id, fetch_arxiv_metadata, is_arxiv_url

router = APIRouter(prefix="/papers", tags=["papers"])


def _toast_headers(message: str, toast_type: str = "success") -> dict:
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": toast_type}})}


def _get_paper(paper_id: int) -> dict | None:
    return row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))


def _get_paper_note(paper_id: int) -> dict | None:
    return row_to_dict(fetch_one("SELECT * FROM paper_notes WHERE paper_id = ?", (paper_id,)))


def _get_paper_topics(paper_id: int) -> list[dict]:
    rows = fetch_all(
        """SELECT t.id, t.name, tp.relevance_score
           FROM topics t JOIN topic_papers tp ON t.id = tp.topic_id
           WHERE tp.paper_id = ?
           ORDER BY t.name""",
        (paper_id,),
    )
    return [dict(r) for r in rows]


def _ensure_paper_note(paper_id: int) -> None:
    """Create a paper_notes row if one doesn't exist yet."""
    existing = fetch_one("SELECT id FROM paper_notes WHERE paper_id = ?", (paper_id,))
    if not existing:
        execute("INSERT INTO paper_notes (paper_id) VALUES (?)", (paper_id,))


def _extract_pdf_metadata(file_path: str) -> dict:
    """Extract title and first page text from a PDF via pypdf."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        meta = reader.metadata or {}
        title = meta.get("/Title", "") or ""
        abstract = ""
        if reader.pages:
            first_page_text = reader.pages[0].extract_text() or ""
            abstract = first_page_text[:2000].strip()
        return {"title": str(title).strip(), "abstract": abstract}
    except Exception:
        return {"title": "", "abstract": ""}


# ---------- Paper list ----------

@router.get("", response_class=HTMLResponse)
async def list_papers(request: Request, topic_id: str | None = None):
    """List all papers, optionally filtered by topic."""
    # topic_id comes as empty string from the "All topics" dropdown option
    topic_id_int = int(topic_id) if topic_id and topic_id.strip().isdigit() else None
    if topic_id_int:
        rows = fetch_all(
            """SELECT p.*, tp.relevance_score
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               ORDER BY p.created_at DESC""",
            (topic_id_int,),
        )
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id_int,)))
    else:
        rows = fetch_all("SELECT p.*, NULL as relevance_score FROM papers p ORDER BY p.created_at DESC")
        topic = None

    papers = [row_to_dict(r) for r in rows]
    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]

    return templates.TemplateResponse(
        "papers/list.html",
        {"request": request, "papers": papers, "topic": topic, "all_topics": all_topics, "topic_id": topic_id_int},
    )


# ---------- Manual paper upload (before /{paper_id} to avoid route conflict) ----------

@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request, topic_id: int | None = None):
    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]
    preselected_ids = [topic_id] if topic_id else []
    return templates.TemplateResponse(
        "papers/upload_form.html",
        {"request": request, "all_topics": all_topics, "preselected_ids": preselected_ids},
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_paper(
    request: Request,
    title: str = Form(""),
    authors: str = Form(""),
    abstract: str = Form(""),
    url: str = Form(""),
    published_venue: str = Form(""),
    published_date: str = Form(""),
    topic_ids: list[int] = Form(default=[]),
    pdf_file: UploadFile | None = File(None),
):
    title = title.strip()
    url = url.strip()
    abstract = abstract.strip()
    authors_list = [a.strip() for a in authors.split(",") if a.strip()]
    pdf_local_path = None
    pdf_url = ""

    # Handle PDF upload
    if pdf_file and pdf_file.filename and pdf_file.size and pdf_file.size > 0:
        pdf_dir = Path(settings.pdf_dir)
        pdf_dir.mkdir(parents=True, exist_ok=True)
        file_id = str(uuid.uuid4())
        dest = pdf_dir / f"{file_id}.pdf"
        content = await pdf_file.read()
        dest.write_bytes(content)
        pdf_local_path = str(dest)

        if not title or not abstract:
            meta = _extract_pdf_metadata(str(dest))
            if not title:
                title = meta["title"]
            if not abstract:
                abstract = meta["abstract"]

    # arXiv URL auto-extraction
    source = "manual"
    source_id_value = str(uuid.uuid4())
    if url and is_arxiv_url(url):
        arxiv_id = extract_arxiv_id(url)
        if arxiv_id:
            meta = await fetch_arxiv_metadata(arxiv_id)
            if meta:
                if not title:
                    title = meta["title"]
                if not authors_list:
                    authors_list = meta["authors"]
                if not abstract:
                    abstract = meta["abstract"]
                if not published_date.strip():
                    published_date = meta["published_date"]
                if not pdf_url:
                    pdf_url = meta["pdf_url"]
                url = meta["url"]
                source = "arxiv"
                source_id_value = arxiv_id

    if not title:
        error_msg = "Title is required. Please provide a title or upload a PDF with metadata."
        if url and is_arxiv_url(url):
            error_msg = "Could not fetch metadata from arXiv. Please fill in the title manually."
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers(error_msg, "error"))
        return resp

    # Dedup check: by source_id (for arxiv), then by URL, then by title
    existing = None
    if source == "arxiv":
        existing = row_to_dict(
            fetch_one("SELECT * FROM papers WHERE source = 'arxiv' AND source_id = ?", (source_id_value,))
        )
    if not existing and url:
        existing = row_to_dict(fetch_one("SELECT * FROM papers WHERE url = ?", (url,)))
    if not existing and title:
        existing = row_to_dict(fetch_one("SELECT * FROM papers WHERE title = ?", (title,)))

    if existing:
        paper_id = existing["id"]
        msg = f"Paper '{existing['title']}' already exists"
    else:
        paper_id = execute(
            """INSERT INTO papers (source, source_id, title, authors, published_venue,
               published_date, abstract, url, pdf_url, pdf_local_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                source_id_value,
                title,
                json.dumps(authors_list),
                published_venue.strip(),
                published_date.strip() or None,
                abstract,
                url,
                pdf_url,
                pdf_local_path,
            ),
        )
        _ensure_paper_note(paper_id)
        msg = f"Paper '{title}' uploaded"

    # Link to all selected topics
    linked_count = 0
    for tid in topic_ids:
        already = fetch_one(
            "SELECT id FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
            (tid, paper_id),
        )
        if not already:
            execute("INSERT INTO topic_papers (topic_id, paper_id) VALUES (?, ?)", (tid, paper_id))
            linked_count += 1
    if linked_count:
        msg += f" — linked to {linked_count} topic{'s' if linked_count > 1 else ''}"

    if request.headers.get("HX-Request"):
        resp = HTMLResponse("")
        resp.headers["HX-Redirect"] = f"/papers/{paper_id}"
        resp.headers.update(_toast_headers(msg))
        return resp

    return RedirectResponse(f"/papers/{paper_id}", status_code=303)


# ---------- Paper detail ----------

@router.get("/{paper_id}", response_class=HTMLResponse)
async def view_paper(request: Request, paper_id: int):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)

    _ensure_paper_note(paper_id)
    note = _get_paper_note(paper_id)
    topics = _get_paper_topics(paper_id)

    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]
    linked_topic_ids = {t["id"] for t in topics}

    return templates.TemplateResponse(
        "papers/detail.html",
        {
            "request": request,
            "paper": paper,
            "paper_id": paper_id,
            "note": note,
            "topics": topics,
            "all_topics": all_topics,
            "linked_topic_ids": linked_topic_ids,
        },
    )


# ---------- Delete paper ----------

@router.delete("/{paper_id}", response_class=HTMLResponse)
async def delete_paper(request: Request, paper_id: int):
    paper = _get_paper(paper_id)
    paper_title = paper["title"] if paper else "Paper"
    execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    resp = HTMLResponse("")
    resp.headers.update(_toast_headers(f"'{paper_title}' deleted"))
    return resp


# ---------- Paper info (read partial / edit form) ----------

@router.get("/{paper_id}/info", response_class=HTMLResponse)
async def get_paper_info(request: Request, paper_id: int, edit: str | None = None):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)
    editing = edit == "1"
    return templates.TemplateResponse(
        "papers/partials/paper_info.html",
        {"request": request, "paper": paper, "editing": editing},
    )


@router.put("/{paper_id}/info", response_class=HTMLResponse)
async def update_paper_info(
    request: Request,
    paper_id: int,
    title: str = Form(""),
    authors: str = Form(""),
    published_venue: str = Form(""),
    published_date: str = Form(""),
    url: str = Form(""),
    abstract: str = Form(""),
):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)

    title = title.strip()
    if not title:
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("Title is required", "error"))
        return resp

    authors_list = [a.strip() for a in authors.split(",") if a.strip()]
    execute(
        """UPDATE papers
           SET title=?, authors=?, published_venue=?, published_date=?, url=?, abstract=?
           WHERE id=?""",
        (
            title,
            json.dumps(authors_list),
            published_venue.strip(),
            published_date.strip() or None,
            url.strip(),
            abstract.strip(),
            paper_id,
        ),
    )

    paper = _get_paper(paper_id)
    resp = templates.TemplateResponse(
        "papers/partials/paper_info.html",
        {"request": request, "paper": paper, "editing": False},
    )
    resp.headers.update(_toast_headers("Paper info updated"))
    return resp


# ---------- Human note update ----------

@router.put("/{paper_id}/human-note", response_class=HTMLResponse)
async def update_human_note(request: Request, paper_id: int, human_note: str = Form("")):
    _ensure_paper_note(paper_id)
    execute(
        "UPDATE paper_notes SET human_note = ?, updated_at = datetime('now') WHERE paper_id = ?",
        (human_note.strip() or None, paper_id),
    )
    note = _get_paper_note(paper_id)
    resp = templates.TemplateResponse(
        "papers/partials/human_note.html",
        {"request": request, "paper_id": paper_id, "note": note},
    )
    resp.headers.update(_toast_headers("Note saved"))
    return resp


# ---------- Add paper to another topic ----------

@router.post("/{paper_id}/add-to-topic", response_class=HTMLResponse)
async def add_to_topic(request: Request, paper_id: int, topic_id: int = Form(...)):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)

    linked = fetch_one(
        "SELECT id FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
        (topic_id, paper_id),
    )
    if linked:
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("Already linked to that topic", "info"))
        return resp

    execute(
        "INSERT INTO topic_papers (topic_id, paper_id) VALUES (?, ?)",
        (topic_id, paper_id),
    )

    topic = row_to_dict(fetch_one("SELECT name FROM topics WHERE id = ?", (topic_id,)))
    topic_name = topic["name"] if topic else "topic"

    topics = _get_paper_topics(paper_id)
    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]
    linked_topic_ids = {t["id"] for t in topics}

    resp = templates.TemplateResponse(
        "papers/partials/paper_topics.html",
        {
            "request": request,
            "paper_id": paper_id,
            "topics": topics,
            "all_topics": all_topics,
            "linked_topic_ids": linked_topic_ids,
        },
    )
    resp.headers.update(_toast_headers(f"Added to '{topic_name}'"))
    return resp
