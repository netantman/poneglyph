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

    # Fetch papers linked to this topic
    paper_rows = fetch_all(
        """SELECT p.* FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           WHERE tp.topic_id = ?
           ORDER BY p.created_at DESC
           LIMIT 20""",
        (topic_id,),
    )
    papers = [row_to_dict(r) for r in paper_rows]

    return templates.TemplateResponse(
        "topics/detail.html", {"request": request, "topic": topic, "papers": papers}
    )


# ---------- Edit topic form ----------

@router.get("/{topic_id}/edit", response_class=HTMLResponse)
async def edit_topic_form(request: Request, topic_id: int):
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)
    return templates.TemplateResponse(
        "topics/form.html", {"request": request, "topic": topic, "editing": True}
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
