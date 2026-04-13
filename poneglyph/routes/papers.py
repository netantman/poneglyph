"""Paper list, detail, manual upload, and human note routes."""

import json
import uuid
from html import escape
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from poneglyph.templating import templates
from poneglyph.config import settings
from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.services.arxiv_fetch import extract_arxiv_id, fetch_arxiv_metadata, is_arxiv_url
from poneglyph.services.crossref_fetch import extract_doi, fetch_crossref_metadata, is_doi_url, search_by_title
from poneglyph.services.pdf_manager import (
    build_pdf_filename, copy_to_working_papers, get_pdf_base_dir, list_pdf_files,
    list_subfolders, move_pdf, save_pdf,
)
from poneglyph.services.llm_metadata import extract_metadata_from_pdf

router = APIRouter(prefix="/papers", tags=["papers"])


def _toast_headers(message: str, toast_type: str = "success") -> dict:
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": toast_type}})}


def _get_paper(paper_id: int) -> dict | None:
    return row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))


def _get_paper_note(paper_id: int) -> dict | None:
    return row_to_dict(fetch_one("SELECT * FROM paper_notes WHERE paper_id = ?", (paper_id,)))


def _get_paper_topics(paper_id: int) -> list[dict]:
    rows = fetch_all(
        """SELECT t.id, t.name, tp.relevance_score, tp.is_scout_seed
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



# ---------- Paper list ----------

@router.get("", response_class=HTMLResponse)
async def list_papers(
    request: Request,
    topic_id: str | None = None,
    read_next: str | None = None,
    q: str = "",
):
    """List all papers, optionally filtered by topic, read_next flag, and/or search query."""
    topic_id_int = int(topic_id) if topic_id and topic_id.strip().isdigit() else None
    read_next_filter = read_next == "1"
    q = q.strip()

    if topic_id_int:
        sql = """SELECT p.*, tp.relevance_score
                 FROM papers p
                 JOIN topic_papers tp ON p.id = tp.paper_id
                 WHERE tp.topic_id = ?"""
        params: list = [topic_id_int]
        if read_next_filter:
            sql += " AND p.read_next = 1"
        if q:
            sql += " AND (LOWER(p.title) LIKE ? OR LOWER(p.authors) LIKE ?)"
            like = f"%{q.lower()}%"
            params += [like, like]
        sql += " ORDER BY p.created_at DESC"
        rows = fetch_all(sql, tuple(params))
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id_int,)))
    else:
        conditions: list[str] = []
        params = []
        if read_next_filter:
            conditions.append("p.read_next = 1")
        if q:
            conditions.append("(LOWER(p.title) LIKE ? OR LOWER(p.authors) LIKE ?)")
            like = f"%{q.lower()}%"
            params += [like, like]
        sql = "SELECT p.*, NULL as relevance_score FROM papers p"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY p.created_at DESC"
        rows = fetch_all(sql, tuple(params))
        topic = None

    papers = [row_to_dict(r) for r in rows]
    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]

    ctx = {
        "request": request,
        "papers": papers,
        "topic": topic,
        "all_topics": all_topics,
        "topic_id": topic_id_int,
        "read_next_filter": read_next_filter,
        "q": q,
    }

    # HTMX partial: return only the table rows when live-searching
    if request.headers.get("HX-Target") == "papers-table-wrapper":
        return templates.TemplateResponse("papers/partials/papers_table.html", ctx)

    return templates.TemplateResponse("papers/list.html", ctx)


# ---------- Manual paper upload (before /{paper_id} to avoid route conflict) ----------

@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request, topic_id: int | None = None):
    all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]
    preselected_ids = [topic_id] if topic_id else []
    subfolders = list_subfolders()
    return templates.TemplateResponse(
        "papers/upload_form.html",
        {"request": request, "all_topics": all_topics, "preselected_ids": preselected_ids, "subfolders": subfolders},
    )


@router.get("/pdf/files", response_class=HTMLResponse)
async def list_pdf_files_in_subfolder(pdf_existing_subfolder: str = ""):
    """Return <option> elements for PDFs in the given subfolder (used by HTMX)."""
    if not pdf_existing_subfolder.strip():
        return HTMLResponse('<option value="">— Select a folder first —</option>')
    files = list_pdf_files(pdf_existing_subfolder.strip())
    if not files:
        return HTMLResponse('<option value="">— No PDFs found —</option>')
    options = '<option value="">— Select file —</option>'
    for name in files:
        options += f'<option value="{name}">{name}</option>'
    return HTMLResponse(options)


@router.get("/search-by-title", response_class=HTMLResponse)
async def search_paper_by_title(title: str = ""):
    """HTMX: search CrossRef by title, return a metadata result card."""
    title = title.strip()
    if not title:
        return HTMLResponse("")
    meta = await search_by_title(title)
    if not meta:
        return HTMLResponse(
            '<div style="background:var(--pico-card-sectioning-background-color);'
            'border:1px solid var(--pico-del-color);border-radius:0.4rem;'
            'padding:0.65rem 0.85rem;font-size:0.9rem;">'
            '&#9888;&#65039; No DOI found for that title. '
            'Please fill in the title, authors, abstract and other fields manually, then submit.'
            '</div>'
        )
    authors_list = meta.get("authors") or []
    authors_str = ", ".join(authors_list)
    year = (meta.get("published_date") or "")[:4]
    venue = meta.get("published_venue") or ""
    abstract_full = meta.get("abstract") or ""
    abstract_snip = abstract_full[:300] + ("..." if len(abstract_full) > 300 else "")
    subtitle_parts = [p for p in [authors_str, year, venue] if p]
    abstract_html = (
        f'<p style="margin-bottom:0.5rem;font-size:0.85rem;">{escape(abstract_snip)}</p>'
        if abstract_snip else ""
    )
    card = f"""<div class="doi-result-card" style="border:1px solid var(--pico-form-element-border-color);
border-radius:0.5rem;padding:0.75rem;background:var(--pico-card-sectioning-background-color);margin-top:0.25rem;"
     data-meta-title="{escape(meta['title'])}"
     data-meta-authors="{escape(authors_str)}"
     data-meta-abstract="{escape(abstract_full)}"
     data-meta-published-date="{escape(meta.get('published_date',''))}"
     data-meta-published-venue="{escape(venue)}"
     data-meta-url="{escape(meta.get('url',''))}">
  <p style="margin-bottom:0.25rem;"><strong>{escape(meta['title'])}</strong></p>
  <p style="margin-bottom:0.25rem;font-size:0.85rem;color:var(--pico-muted-color);">{escape(' · '.join(subtitle_parts))}</p>
  {abstract_html}
  <button type="button" class="outline" style="margin:0;width:auto;font-size:0.85rem;padding:0.3rem 0.7rem;"
          onclick="fillSearchedMetadata(this.closest('.doi-result-card'))">Use this metadata</button>
</div>"""
    return HTMLResponse(card)


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
    pdf_mode: str = Form("upload"),
    pdf_file: UploadFile | None = File(None),
    pdf_subfolder: str = Form(""),
    pdf_existing_subfolder: str = Form(""),
    pdf_existing_filename: str = Form(""),
    pdf_tmp_id: str = Form(""),  # carry temp PDF across the LLM-failure re-render
):
    title = title.strip()
    url = url.strip()
    abstract = abstract.strip()
    pdf_tmp_id = pdf_tmp_id.strip()
    authors_list = [a.strip() for a in authors.split(",") if a.strip()]
    pdf_url = ""

    # Resolve PDF source: uploaded file, link to existing file, or previously saved temp
    has_uploaded = pdf_file and pdf_file.filename and pdf_file.size and pdf_file.size > 0
    pdf_content: bytes | None = None
    linked_pdf_path: Path | None = None
    pdf_tmp_path: Path | None = None  # persistent temp file (for new uploads)

    if pdf_mode == "upload" and has_uploaded:
        pdf_content = await pdf_file.read()
        # Persist to temp immediately so we can survive a re-render round-trip
        tmp_dir = Path("data/pdfs/tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        new_tmp_id = str(uuid.uuid4())
        pdf_tmp_path = tmp_dir / f"{new_tmp_id}.pdf"
        pdf_tmp_path.write_bytes(pdf_content)
        pdf_tmp_id = new_tmp_id  # will be included in re-rendered form if needed
    elif pdf_tmp_id:
        # Re-submission after LLM failure: load PDF from previously saved temp
        cand = Path("data/pdfs/tmp") / f"{pdf_tmp_id}.pdf"
        if cand.exists():
            pdf_tmp_path = cand
            pdf_content = cand.read_bytes()
    elif pdf_mode == "link" and pdf_existing_subfolder.strip() and pdf_existing_filename.strip():
        candidate = get_pdf_base_dir() / pdf_existing_subfolder.strip() / pdf_existing_filename.strip()
        if candidate.exists():
            linked_pdf_path = candidate

    pdf_path_for_extraction: Path | None = linked_pdf_path or pdf_tmp_path
    has_any_pdf = bool(pdf_content or linked_pdf_path)

    # Priority 2: source API metadata (arXiv or DOI/CrossRef)
    source = "manual"
    source_id_value = str(uuid.uuid4())
    meta_source_msg: str | None = None
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
                meta_source_msg = "Metadata from arXiv"
    elif url and is_doi_url(url):
        doi = extract_doi(url)
        if doi:
            meta = await fetch_crossref_metadata(doi)
            if meta:
                if not title:
                    title = meta["title"]
                if not authors_list:
                    authors_list = meta["authors"]
                if not abstract:
                    abstract = meta["abstract"]
                if not published_date.strip():
                    published_date = meta["published_date"]
                if not published_venue.strip():
                    published_venue = meta["published_venue"]
                url = meta["url"]
                source = "doi"
                source_id_value = doi
                meta_source_msg = "Metadata from CrossRef"

    # Priority 3: LLM extraction from PDF
    # Skip if this is a re-submission (pdf_tmp_id was present on incoming request, meaning user
    # is now providing data manually after the first attempt failed).
    is_resubmit = bool(pdf_tmp_id and not has_uploaded)
    if has_any_pdf and source == "manual" and not is_resubmit and (
        not title or not abstract or not authors_list
    ):
        llm_meta = await extract_metadata_from_pdf(pdf_path_for_extraction) if pdf_path_for_extraction else {}
        if llm_meta:
            if not title:
                title = str(llm_meta.get("title", "")).strip()
            if not authors_list:
                raw = llm_meta.get("authors", [])
                authors_list = raw if isinstance(raw, list) else []
            if not abstract:
                abstract = str(llm_meta.get("abstract", "")).strip()
            if not published_date.strip():
                published_date = str(llm_meta.get("published_date", "") or "").strip()
            if not published_venue.strip():
                published_venue = str(llm_meta.get("published_venue", "") or "").strip()
            if title or abstract:
                meta_source_msg = "Metadata extracted from PDF"

    # Priority 4: auto-resolve DOI via CrossRef title search for manual papers with no URL yet
    if source == "manual" and title and not url:
        doi_meta = await search_by_title(title)
        if doi_meta:
            url = doi_meta.get("url", "")
            if not authors_list:
                authors_list = doi_meta.get("authors") or []
            if not abstract:
                abstract = doi_meta.get("abstract") or ""
            if not published_date.strip():
                published_date = doi_meta.get("published_date") or ""
            if not published_venue.strip():
                published_venue = doi_meta.get("published_venue") or ""
            if meta_source_msg:
                meta_source_msg += " + DOI resolved"
            else:
                meta_source_msg = "DOI resolved from title"

    # If title still missing after LLM and we have a PDF, re-render form with inline prompt
    if not title and pdf_tmp_path:
        all_topics = [row_to_dict(r) for r in fetch_all("SELECT id, name FROM topics ORDER BY name")]
        subfolders = list_subfolders()
        return templates.TemplateResponse(
            "papers/upload_form.html",
            {
                "request": request,
                "all_topics": all_topics,
                "preselected_ids": topic_ids,
                "subfolders": subfolders,
                "pdf_tmp_id": pdf_tmp_id,
                "pdf_subfolder_selected": pdf_subfolder.strip(),
                "llm_message": (
                    "Could not extract the title from the PDF automatically. "
                    "Enter the title below — you can search CrossRef to auto-fill the rest of the metadata."
                ),
                "prefill": {
                    "authors": ", ".join(authors_list),
                    "abstract": abstract,
                    "published_date": published_date.strip(),
                    "published_venue": published_venue.strip(),
                    "url": url,
                },
            },
        )

    if not title:
        if url and is_arxiv_url(url):
            error_msg = "Could not fetch metadata from arXiv — please fill in the title manually."
        else:
            error_msg = "Title is required."
        resp = HTMLResponse("")
        resp.headers["HX-Reswap"] = "none"
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
        meta_source_msg = None  # suppress for duplicates
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
                None,  # pdf_local_path set below after save
            ),
        )
        _ensure_paper_note(paper_id)
        msg = f"Paper '{title}' uploaded"
        if meta_source_msg:
            msg += f" — {meta_source_msg}"

    # Save / link PDF
    if pdf_content and pdf_subfolder.strip():
        year = (published_date.strip()[:4]) if published_date.strip() else None
        filename = build_pdf_filename(pdf_subfolder.strip(), title, authors_list, year)
        dest = save_pdf(pdf_content, pdf_subfolder.strip(), filename)
        execute("UPDATE papers SET pdf_local_path = ? WHERE id = ?", (str(dest), paper_id))
    elif linked_pdf_path:
        execute("UPDATE papers SET pdf_local_path = ? WHERE id = ?", (str(linked_pdf_path), paper_id))

    # Clean up temp file (new upload or carried-over from previous attempt)
    if pdf_tmp_path and pdf_tmp_path.exists():
        pdf_tmp_path.unlink(missing_ok=True)

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


# ---------- Read Next toggle ----------

@router.post("/{paper_id}/read-next", response_class=HTMLResponse)
async def toggle_read_next(request: Request, paper_id: int):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("", status_code=404)
    new_val = 0 if paper.get("read_next") else 1
    execute("UPDATE papers SET read_next = ? WHERE id = ?", (new_val, paper_id))
    label = "Flagged for reading" if new_val else "Flag removed"
    icon_html = _read_next_icon(paper_id, new_val)
    resp = HTMLResponse(icon_html)
    resp.headers.update(_toast_headers(label))
    return resp


def _read_next_icon(paper_id: int, read_next: int) -> str:
    filled = "🔖" if read_next else "📄"
    title = "Remove from Read Next" if read_next else "Mark as Read Next"
    return (
        f'<span hx-post="/papers/{paper_id}/read-next" hx-swap="outerHTML" '
        f'style="cursor:pointer; font-size:1.1rem;" title="{title}">{filled}</span>'
    )


# ---------- Unprocessed toggle ----------

def _unprocessed_toggle_html(paper_id: int, unprocessed: int) -> str:
    if unprocessed:
        style = (
            "border-color:#f59e0b;color:#f59e0b;"
            "margin:0;width:auto;font-size:0.85rem;padding:0.3rem 0.7rem;"
        )
        label = "&#9711; Unprocessed"
        title = "Mark as processed"
    else:
        style = "margin:0;width:auto;font-size:0.85rem;padding:0.3rem 0.7rem;"
        label = "&#10003; Processed"
        title = "Mark as unprocessed"
    return (
        f'<button class="outline" style="{style}" '
        f'hx-post="/papers/{paper_id}/unprocessed" '
        f'hx-target="this" hx-swap="outerHTML" '
        f'title="{title}">{label}</button>'
    )


@router.post("/{paper_id}/unprocessed", response_class=HTMLResponse)
async def toggle_unprocessed(request: Request, paper_id: int):
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("", status_code=404)
    new_val = 0 if paper.get("unprocessed") else 1
    execute("UPDATE papers SET unprocessed = ? WHERE id = ?", (new_val, paper_id))
    label = "Marked unprocessed" if new_val else "Marked processed"
    resp = HTMLResponse(_unprocessed_toggle_html(paper_id, new_val))
    resp.headers.update(_toast_headers(label))
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
    pdf_local_path: str = Form(""),
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
    new_pdf_path = pdf_local_path.strip() or paper.get("pdf_local_path") or None
    execute(
        """UPDATE papers
           SET title=?, authors=?, published_venue=?, published_date=?, url=?, abstract=?,
               pdf_local_path=?
           WHERE id=?""",
        (
            title,
            json.dumps(authors_list),
            published_venue.strip(),
            published_date.strip() or None,
            url.strip(),
            abstract.strip(),
            new_pdf_path,
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


# ---------- PDF routes ----------

@router.get("/{paper_id}/pdf/manage", response_class=HTMLResponse)
async def pdf_manage_form(request: Request, paper_id: int):
    """Return the PDF manage dialog (subfolder dropdown + optional file upload)."""
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)

    subfolders = list_subfolders()
    # Determine current subfolder from pdf_local_path
    current_subfolder = ""
    if paper.get("pdf_local_path"):
        from poneglyph.services.pdf_manager import get_pdf_base_dir
        try:
            rel = Path(paper["pdf_local_path"]).relative_to(get_pdf_base_dir())
            current_subfolder = rel.parts[0] if rel.parts else ""
        except ValueError:
            pass

    return templates.TemplateResponse(
        "papers/partials/pdf_manage.html",
        {
            "request": request,
            "paper": paper,
            "subfolders": subfolders,
            "current_subfolder": current_subfolder,
        },
    )


@router.post("/{paper_id}/pdf/manage", response_class=HTMLResponse)
async def pdf_manage_submit(
    request: Request,
    paper_id: int,
    pdf_mode: str = Form("upload"),
    subfolder: str = Form(""),
    pdf_file: UploadFile | None = File(None),
    extract_metadata: str = Form(""),
    pdf_existing_subfolder: str = Form(""),
    pdf_existing_filename: str = Form(""),
):
    """Handle PDF upload, subfolder move, or link to existing file."""
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)

    # --- Link existing file mode ---
    if pdf_mode == "link":
        if not pdf_existing_subfolder.strip() or not pdf_existing_filename.strip():
            resp = HTMLResponse("")
            resp.headers.update(_toast_headers("Please select a folder and file", "error"))
            return resp
        linked = get_pdf_base_dir() / pdf_existing_subfolder.strip() / pdf_existing_filename.strip()
        if not linked.exists():
            resp = HTMLResponse("")
            resp.headers.update(_toast_headers("File not found on disk", "error"))
            return resp
        execute("UPDATE papers SET pdf_local_path = ? WHERE id = ?", (str(linked), paper_id))
        resp = HTMLResponse("<script>setTimeout(()=>window.location.reload(),1500)</script>")
        resp.headers.update(_toast_headers(f"Linked to {pdf_existing_filename.strip()}"))
        return resp

    # --- Upload / move mode ---
    if not subfolder.strip():
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("Please select a folder", "error"))
        return resp

    year = paper.get("published_date", "")[:4] if paper.get("published_date") else None
    authors = paper.get("authors") if isinstance(paper.get("authors"), list) else []
    filename = build_pdf_filename(subfolder, paper["title"], authors, year)

    has_new_file = pdf_file and pdf_file.filename and pdf_file.size and pdf_file.size > 0
    current_path = Path(paper["pdf_local_path"]) if paper.get("pdf_local_path") else None

    if has_new_file:
        content = await pdf_file.read()
        if current_path and current_path.exists():
            current_path.unlink()
        new_path = save_pdf(content, subfolder, filename)
    elif current_path:
        if not current_path.exists():
            resp = HTMLResponse("")
            resp.headers.update(_toast_headers(f"PDF not found at: {current_path}", "error"))
            return resp
        new_path = move_pdf(current_path, subfolder, filename)
    else:
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("No PDF to save — upload a file", "error"))
        return resp

    execute(
        "UPDATE papers SET pdf_local_path = ? WHERE id = ?",
        (str(new_path), paper_id),
    )

    toast_msg = f"PDF saved to {subfolder}/"

    # Optional LLM metadata extraction (only when a new file was uploaded and checkbox checked)
    if has_new_file and extract_metadata == "true":
        llm_meta = await extract_metadata_from_pdf(new_path)
        if llm_meta:
            new_title = str(llm_meta.get("title", "") or "").strip() or paper["title"]
            raw_authors = llm_meta.get("authors", [])
            new_authors = raw_authors if isinstance(raw_authors, list) else []
            new_abstract = str(llm_meta.get("abstract", "") or "").strip() or paper.get("abstract", "")
            new_venue = str(llm_meta.get("published_venue", "") or "").strip() or paper.get("published_venue", "")
            new_date = str(llm_meta.get("published_date", "") or "").strip() or paper.get("published_date", "") or None
            execute(
                """UPDATE papers SET title=?, authors=?, abstract=?, published_venue=?,
                   published_date=? WHERE id=?""",
                (new_title, json.dumps(new_authors), new_abstract, new_venue, new_date, paper_id),
            )
            toast_msg += " — Metadata updated from PDF"
        else:
            toast_msg += " — Could not extract metadata"

    resp = HTMLResponse("<script>setTimeout(()=>window.location.reload(),1500)</script>")
    resp.headers.update(_toast_headers(toast_msg))
    return resp


@router.put("/{paper_id}/pdf/path", response_class=HTMLResponse)
async def update_pdf_path(request: Request, paper_id: int, pdf_local_path: str = Form("")):
    """Update pdf_local_path in the DB only — does not move or rename the file."""
    paper = _get_paper(paper_id)
    if not paper:
        return HTMLResponse("<p>Paper not found.</p>", status_code=404)
    new_path = pdf_local_path.strip() or None
    execute("UPDATE papers SET pdf_local_path = ? WHERE id = ?", (new_path, paper_id))
    paper = _get_paper(paper_id)
    full_path = paper.get("pdf_local_path") or ""
    path_is_url = full_path.startswith("http://") or full_path.startswith("https://")
    if full_path:
        path_display = (
            f'<a href="{escape(full_path)}" target="_blank" rel="noopener" style="font-size:0.82rem;">'
            f'{escape(full_path)}</a>'
            if path_is_url
            else f"<code>{escape(full_path)}</code>"
        )
        html = f"""<div id="pdf-path-section" style="margin-bottom:0.5rem;">
    <div id="pdf-path-view" style="display:flex; align-items:center; gap:0.75rem; font-size:0.85rem; color:var(--pico-muted-color);">
        <span>PDF: {path_display}</span>
        <button class="outline secondary" style="font-size:0.78rem; padding:0.2rem 0.5rem; margin:0;"
                onclick="document.getElementById('pdf-path-view').style.display='none';
                         document.getElementById('pdf-path-edit').style.display='flex';">Edit path</button>
    </div>
    <div id="pdf-path-edit" style="display:none; align-items:center; gap:0.5rem; flex-wrap:wrap;">
        <input id="pdf-path-input" type="text" value="{escape(full_path)}"
               placeholder="Full path or OneDrive URL"
               style="flex:1; min-width:20rem; margin:0; font-size:0.85rem;">
        <button style="width:auto; margin:0; font-size:0.85rem; padding:0.3rem 0.7rem;"
                hx-put="/papers/{paper_id}/pdf/path"
                hx-vals="js:{{pdf_local_path: document.getElementById('pdf-path-input').value}}"
                hx-target="#pdf-path-section"
                hx-swap="outerHTML">Save</button>
        <button class="outline secondary" style="width:auto; margin:0; font-size:0.85rem; padding:0.3rem 0.7rem;"
                onclick="document.getElementById('pdf-path-view').style.display='flex';
                         document.getElementById('pdf-path-edit').style.display='none';">Cancel</button>
    </div>
</div>"""
    else:
        html = f"""<div id="pdf-path-section" style="margin-bottom:0.5rem;">
    <div id="pdf-path-view" style="display:flex; align-items:center; gap:0.75rem; font-size:0.85rem; color:var(--pico-muted-color);">
        <span>PDF: <em>not linked</em></span>
        <button class="outline secondary" style="font-size:0.78rem; padding:0.2rem 0.5rem; margin:0;"
                onclick="document.getElementById('pdf-path-view').style.display='none';
                         document.getElementById('pdf-path-edit').style.display='flex';">Set path</button>
    </div>
    <div id="pdf-path-edit" style="display:none; align-items:center; gap:0.5rem; flex-wrap:wrap;">
        <input id="pdf-path-input" type="text" value=""
               placeholder="Full path or OneDrive URL"
               style="flex:1; min-width:20rem; margin:0; font-size:0.85rem;">
        <button style="width:auto; margin:0; font-size:0.85rem; padding:0.3rem 0.7rem;"
                hx-put="/papers/{paper_id}/pdf/path"
                hx-vals="js:{{pdf_local_path: document.getElementById('pdf-path-input').value}}"
                hx-target="#pdf-path-section"
                hx-swap="outerHTML">Save</button>
        <button class="outline secondary" style="width:auto; margin:0; font-size:0.85rem; padding:0.3rem 0.7rem;"
                onclick="document.getElementById('pdf-path-view').style.display='flex';
                         document.getElementById('pdf-path-edit').style.display='none';">Cancel</button>
    </div>
</div>"""
    resp = HTMLResponse(html)
    resp.headers.update(_toast_headers("PDF path updated"))
    return resp


@router.post("/{paper_id}/pdf/save", response_class=HTMLResponse)
async def save_pdf_to_desktop(paper_id: int):
    """Copy the PDF to ~/Desktop/poneglyph_working_papers/ and return a toast."""
    paper = _get_paper(paper_id)
    if not paper or not paper.get("pdf_local_path"):
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("No PDF available", "error"))
        return resp
    raw = paper["pdf_local_path"]
    if raw.startswith("http://") or raw.startswith("https://"):
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers("Path is a URL — use Open Link to access it", "error"))
        return resp
    pdf_path = Path(raw)
    if not pdf_path.exists():
        resp = HTMLResponse("")
        resp.headers.update(_toast_headers(f"PDF not found at: {paper['pdf_local_path']}", "error"))
        return resp

    year = paper.get("published_date", "")[:4] if paper.get("published_date") else None
    authors = paper.get("authors") if isinstance(paper.get("authors"), list) else []
    # Determine subfolder for naming convention
    from poneglyph.services.pdf_manager import get_pdf_base_dir
    try:
        rel = pdf_path.relative_to(get_pdf_base_dir())
        subfolder = rel.parts[0] if rel.parts else ""
    except ValueError:
        subfolder = ""
    filename = build_pdf_filename(subfolder, paper["title"], authors, year)
    copy_to_working_papers(pdf_path, filename)

    resp = HTMLResponse("")
    resp.headers.update(_toast_headers("PDF saved to poneglyph_working_papers/"))
    return resp


# ---------- Human note update ----------

@router.put("/{paper_id}/human-note", response_class=HTMLResponse)
async def update_human_note(request: Request, paper_id: int, human_note: str = Form("")):
    _ensure_paper_note(paper_id)
    # Store raw HTML from Quill; treat empty-editor sentinel as blank
    stored = human_note if human_note not in ("", "<p><br></p>") else None
    execute(
        "UPDATE paper_notes SET human_note = ?, updated_at = datetime('now') WHERE paper_id = ?",
        (stored, paper_id),
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
