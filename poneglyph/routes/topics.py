"""Topic CRUD routes – full-page views + htmx partial responses."""

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

# Toast helper: returns HX-Trigger header for showToast event
def _toast_headers(message: str, toast_type: str = "success") -> dict:
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": toast_type}})}

from poneglyph.templating import templates
from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.models import parse_comma_list, parse_newline_list

router = APIRouter(prefix="/topics", tags=["topics"])


# ---------- helpers ----------

def _topic_row(topic_id: int) -> dict | None:
    return row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))


# ---------- List all topics ----------

@router.get("", response_class=HTMLResponse)
async def list_topics(request: Request):
    rows = fetch_all("SELECT * FROM topics ORDER BY created_at DESC")
    topics = [row_to_dict(r) for r in rows]
    return templates.TemplateResponse(
        "topics/list.html", {"request": request, "topics": topics}
    )


# ---------- New topic form ----------

@router.get("/new", response_class=HTMLResponse)
async def new_topic_form(request: Request):
    """Return the create-topic form. If htmx, return partial; else full page."""
    template = "topics/form.html"
    ctx = {"request": request, "topic": None, "editing": False}
    return templates.TemplateResponse(template, ctx)


# ---------- Create topic ----------

@router.post("", response_class=HTMLResponse)
async def create_topic(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    keywords: str = Form(""),
    priority_keywords: str = Form(""),
    problem_statements: str = Form(""),
    sources: list[str] = Form(default=["arxiv"]),
    pdf_policy: str = Form("link_only"),
):
    kw_list = parse_comma_list(keywords)
    pkw_list = parse_comma_list(priority_keywords)
    ps_list = parse_newline_list(problem_statements)

    topic_id = execute(
        """INSERT INTO topics (name, description, keywords, priority_keywords,
           problem_statements, sources, pdf_policy)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            description.strip(),
            json.dumps(kw_list),
            json.dumps(pkw_list),
            json.dumps(ps_list),
            json.dumps(sources),
            pdf_policy,
        ),
    )

    # If htmx request, return the new row partial to swap in
    if request.headers.get("HX-Request"):
        topic = _topic_row(topic_id)
        resp = templates.TemplateResponse(
            "topics/partials/topic_row.html",
            {"request": request, "topic": topic},
        )
        resp.headers.update(_toast_headers(f"Topic '{name.strip()}' created"))
        return resp

    # Otherwise redirect to list
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/topics", status_code=303)


# ---------- View single topic ----------

@router.get("/{topic_id}", response_class=HTMLResponse)
async def view_topic(request: Request, topic_id: int):
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    # Fetch papers linked to this topic, including scout-seed flag
    paper_rows = fetch_all(
        """SELECT p.*, tp.is_scout_seed
           FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           WHERE tp.topic_id = ?
           ORDER BY p.read_next DESC, p.created_at DESC
           LIMIT 20""",
        (topic_id,),
    )
    papers = [row_to_dict(r) for r in paper_rows]
    seed_count = sum(1 for p in papers if p.get("is_scout_seed"))

    return templates.TemplateResponse(
        "topics/detail.html",
        {"request": request, "topic": topic, "papers": papers, "seed_count": seed_count},
    )


# ---------- Edit topic form ----------

@router.get("/{topic_id}/edit", response_class=HTMLResponse)
async def edit_topic_form(request: Request, topic_id: int, from_detail: int = 0):
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)
    return templates.TemplateResponse(
        "topics/form.html",
        {"request": request, "topic": topic, "editing": True, "from_detail": bool(from_detail)},
    )


# ---------- Update topic ----------

@router.put("/{topic_id}", response_class=HTMLResponse)
async def update_topic(
    request: Request,
    topic_id: int,
    name: str = Form(...),
    description: str = Form(""),
    keywords: str = Form(""),
    priority_keywords: str = Form(""),
    problem_statements: str = Form(""),
    sources: list[str] = Form(default=["arxiv"]),
    pdf_policy: str = Form("link_only"),
    is_active: bool = Form(True),
    from_detail: int = Form(0),
):
    old_topic = _topic_row(topic_id)
    if not old_topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    kw_list = parse_comma_list(keywords)
    pkw_list = parse_comma_list(priority_keywords)
    ps_list = parse_newline_list(problem_statements)

    execute(
        """UPDATE topics
           SET name=?, description=?, keywords=?, priority_keywords=?,
               problem_statements=?, sources=?, pdf_policy=?, is_active=?,
               updated_at=datetime('now')
           WHERE id=?""",
        (
            name.strip(),
            description.strip(),
            json.dumps(kw_list),
            json.dumps(pkw_list),
            json.dumps(ps_list),
            json.dumps(sources),
            pdf_policy,
            int(is_active),
            topic_id,
        ),
    )

    # Log steering changes
    _log_steering_change(topic_id, old_topic, kw_list, pkw_list, ps_list)

    if request.headers.get("HX-Request"):
        if from_detail:
            # Loaded from topic detail page — redirect back to it
            resp = HTMLResponse("")
            resp.headers["HX-Redirect"] = f"/topics/{topic_id}"
            resp.headers.update(_toast_headers(f"Topic '{name.strip()}' updated"))
            return resp
        topic = _topic_row(topic_id)
        resp = templates.TemplateResponse(
            "topics/partials/topic_row.html",
            {"request": request, "topic": topic},
        )
        resp.headers.update(_toast_headers(f"Topic '{name.strip()}' updated"))
        return resp

    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


# ---------- Delete topic ----------

@router.delete("/{topic_id}", response_class=HTMLResponse)
async def delete_topic(request: Request, topic_id: int):
    topic = _topic_row(topic_id)
    topic_name = topic["name"] if topic else "Topic"
    execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    resp = HTMLResponse("")
    resp.headers.update(_toast_headers(f"'{topic_name}' deleted"))
    return resp


# ---------- Toggle active ----------

@router.post("/{topic_id}/toggle", response_class=HTMLResponse)
async def toggle_topic(request: Request, topic_id: int):
    execute(
        "UPDATE topics SET is_active = NOT is_active, updated_at=datetime('now') WHERE id=?",
        (topic_id,),
    )
    topic = _topic_row(topic_id)
    status = "resumed" if topic["is_active"] else "paused"
    resp = templates.TemplateResponse(
        "topics/partials/topic_row.html",
        {"request": request, "topic": topic},
    )
    resp.headers.update(_toast_headers(f"'{topic['name']}' {status}"))
    return resp


# ---------- Topic papers list (full, htmx expand) ----------

@router.get("/{topic_id}/papers-list", response_class=HTMLResponse)
async def topic_papers_list(request: Request, topic_id: int, q: str = ""):
    """Return papers for a topic as an htmx partial (no LIMIT, optional search)."""
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)
    q = q.strip()
    if q:
        like = f"%{q.lower()}%"
        paper_rows = fetch_all(
            """SELECT p.*, tp.is_scout_seed
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               AND (LOWER(p.title) LIKE ? OR LOWER(p.authors) LIKE ?)
               ORDER BY p.read_next DESC, p.created_at DESC""",
            (topic_id, like, like),
        )
    else:
        paper_rows = fetch_all(
            """SELECT p.*, tp.is_scout_seed
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               ORDER BY p.read_next DESC, p.created_at DESC""",
            (topic_id,),
        )
    papers = [row_to_dict(r) for r in paper_rows]
    return templates.TemplateResponse(
        "topics/partials/papers_list.html",
        {"request": request, "topic": topic, "papers": papers, "show_all_link": False},
    )


# ---------- Scout seed toggle ----------

def _seed_icon_html(topic_id: int, paper_id: int, is_seed: int) -> str:
    """Return the htmx-wired seed toggle span for a paper row."""
    style = "cursor:pointer; font-size:1.0rem;" + (
        "" if is_seed else " opacity:0.3;"
    )
    title = "Remove from scout seeds" if is_seed else "Add to scout seeds"
    return (
        f'<span hx-post="/topics/{topic_id}/papers/{paper_id}/toggle-seed" '
        f'hx-swap="outerHTML" style="{style}" title="{title}">🌱</span>'
    )


@router.post("/{topic_id}/papers/{paper_id}/toggle-seed", response_class=HTMLResponse)
async def toggle_scout_seed(request: Request, topic_id: int, paper_id: int):
    row = fetch_one(
        "SELECT is_scout_seed FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
        (topic_id, paper_id),
    )
    if not row:
        return HTMLResponse("", status_code=404)
    new_val = 0 if row["is_scout_seed"] else 1
    execute(
        "UPDATE topic_papers SET is_scout_seed = ? WHERE topic_id = ? AND paper_id = ?",
        (new_val, topic_id, paper_id),
    )
    label = "Added to scout seeds" if new_val else "Removed from scout seeds"
    resp = HTMLResponse(_seed_icon_html(topic_id, paper_id, new_val))
    resp.headers.update(_toast_headers(label))
    return resp


# ---------- steering log helper ----------

def _log_steering_change(
    topic_id: int,
    old: dict,
    new_kw: list[str],
    new_pkw: list[str],
    new_ps: list[str],
) -> None:
    changes = []
    if set(old.get("keywords", [])) != set(new_kw):
        changes.append(f"Keywords changed: {old.get('keywords', [])} -> {new_kw}")
    if set(old.get("priority_keywords", [])) != set(new_pkw):
        changes.append(
            f"Priority keywords changed: {old.get('priority_keywords', [])} -> {new_pkw}"
        )
    if old.get("problem_statements", []) != new_ps:
        changes.append(
            f"Problem statements changed: {old.get('problem_statements', [])} -> {new_ps}"
        )
    if changes:
        execute(
            "INSERT INTO topic_steering_log (topic_id, change_description) VALUES (?, ?)",
            (topic_id, "; ".join(changes)),
        )
