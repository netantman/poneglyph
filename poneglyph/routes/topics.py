"""Topic CRUD routes – full-page views + htmx partial responses."""

import asyncio
import json
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

# Toast helper: returns HX-Trigger header for showToast event
def _toast_headers(message: str, toast_type: str = "success") -> dict:
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": toast_type}})}


async def _run_relevance_update(topic_id: int) -> None:
    from poneglyph.services.relevance import update_topic_relevance_scores
    update_topic_relevance_scores(topic_id)

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
    problem_statements: str = Form(""),
    skim_skill_md: str = Form(""),
    deep_synthesis_skill_md: str = Form(""),
):
    kw_list = parse_comma_list(keywords)
    ps_list = parse_newline_list(problem_statements)

    topic_id = execute(
        """INSERT INTO topics (name, description, keywords, priority_keywords,
           problem_statements, skim_skill_md, deep_synthesis_skill_md)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            description.strip(),
            json.dumps(kw_list),
            json.dumps([]),
            json.dumps(ps_list),
            skim_skill_md.strip() or None,
            deep_synthesis_skill_md.strip() or None,
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

    # Fetch papers linked to this topic, including scout-seed and not_interesting flags
    paper_rows = fetch_all(
        """SELECT p.*, tp.is_scout_seed, tp.not_interesting, tp.relevance_score
           FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           WHERE tp.topic_id = ?
           ORDER BY p.read_next DESC, tp.not_interesting ASC,
                    COALESCE(tp.relevance_score, 1.0) DESC,
                    p.published_date DESC, p.created_at DESC
           LIMIT 20""",
        (topic_id,),
    )
    papers = [row_to_dict(r) for r in paper_rows]
    seed_count = sum(1 for p in papers if p.get("is_scout_seed"))

    latest_synthesis = row_to_dict(fetch_one(
        "SELECT * FROM cross_syntheses WHERE topic_id = ? ORDER BY created_at DESC LIMIT 1",
        (topic_id,),
    ))

    return templates.TemplateResponse(
        "topics/detail.html",
        {
            "request": request,
            "topic": topic,
            "papers": papers,
            "seed_count": seed_count,
            "latest_synthesis": latest_synthesis,
        },
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
    problem_statements: str = Form(""),
    is_active: bool = Form(True),
    from_detail: int = Form(0),
    skim_skill_md: str = Form(""),
    deep_synthesis_skill_md: str = Form(""),
):
    old_topic = _topic_row(topic_id)
    if not old_topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    kw_list = parse_comma_list(keywords)
    ps_list = parse_newline_list(problem_statements)

    execute(
        """UPDATE topics
           SET name=?, description=?, keywords=?, priority_keywords=?,
               problem_statements=?, is_active=?,
               skim_skill_md=?, deep_synthesis_skill_md=?,
               updated_at=datetime('now')
           WHERE id=?""",
        (
            name.strip(),
            description.strip(),
            json.dumps(kw_list),
            json.dumps([]),
            json.dumps(ps_list),
            int(is_active),
            skim_skill_md.strip() or None,
            deep_synthesis_skill_md.strip() or None,
            topic_id,
        ),
    )

    # Log steering changes
    _log_steering_change(topic_id, old_topic, kw_list, [], ps_list)

    if old_topic.get("problem_statements", []) != ps_list:
        from poneglyph.services.relevance import refresh_topic_embeddings, update_topic_relevance_scores
        import asyncio
        topic_for_embed = {**old_topic, "problem_statements": ps_list}
        refresh_topic_embeddings(topic_id, topic_for_embed)
        asyncio.create_task(_run_relevance_update(topic_id))

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
            """SELECT p.*, tp.is_scout_seed, tp.not_interesting, tp.relevance_score
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               AND (LOWER(p.title) LIKE ? OR LOWER(p.authors) LIKE ?)
               ORDER BY p.read_next DESC, tp.not_interesting ASC,
                        COALESCE(tp.relevance_score, 1.0) DESC,
                        p.published_date DESC, p.created_at DESC""",
            (topic_id, like, like),
        )
    else:
        paper_rows = fetch_all(
            """SELECT p.*, tp.is_scout_seed, tp.not_interesting, tp.relevance_score
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               WHERE tp.topic_id = ?
               ORDER BY p.read_next DESC, tp.not_interesting ASC,
                        COALESCE(tp.relevance_score, 1.0) DESC,
                        p.published_date DESC, p.created_at DESC""",
            (topic_id,),
        )
    papers = [row_to_dict(r) for r in paper_rows]
    return templates.TemplateResponse(
        "topics/partials/papers_list.html",
        {"request": request, "topic": topic, "papers": papers, "show_all_link": False},
    )


# ---------- Seed + not-interesting icon helpers ----------

def _tp_icons_html(topic_id: int, paper_id: int, is_seed: int, not_interesting: int) -> str:
    """Return the shared icon container for seed and not-interesting toggles.

    Both toggles target this wrapper (hx-target="#tp-icons-{topic_id}-{paper_id}"),
    so toggling either one refreshes both icons atomically.
    """
    seed_style = (
        "cursor:pointer; font-size:1.0rem; flex-shrink:0; margin-top:0.1rem;"
        + ("" if is_seed else " opacity:0.25;")
    )
    seed_title = "Remove from scout seeds" if is_seed else "Add to scout seeds"

    ni_style = (
        "cursor:pointer; font-size:1.0rem; flex-shrink:0; margin-top:0.1rem;"
        + ("" if not_interesting else " opacity:0.2;")
    )
    ni_title = "Mark as interesting" if not_interesting else "Mark as not interesting for this topic"

    target = f"#tp-icons-{topic_id}-{paper_id}"
    return (
        f'<span id="tp-icons-{topic_id}-{paper_id}" style="display:contents;">'
        f'<span hx-post="/topics/{topic_id}/papers/{paper_id}/toggle-seed" '
        f'hx-target="{target}" hx-swap="outerHTML" '
        f'style="{seed_style}" title="{seed_title}">🌱</span>'
        f'<span hx-post="/topics/{topic_id}/papers/{paper_id}/toggle-not-interesting" '
        f'hx-target="{target}" hx-swap="outerHTML" '
        f'style="{ni_style}" title="{ni_title}">🚫</span>'
        f'</span>'
    )


@router.post("/{topic_id}/papers/{paper_id}/toggle-seed", response_class=HTMLResponse)
async def toggle_scout_seed(request: Request, topic_id: int, paper_id: int):
    row = fetch_one(
        "SELECT is_scout_seed, not_interesting FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
        (topic_id, paper_id),
    )
    if not row:
        return HTMLResponse("", status_code=404)
    # Cannot seed a paper marked not interesting
    if not row["is_scout_seed"] and row["not_interesting"]:
        resp = HTMLResponse(_tp_icons_html(topic_id, paper_id, 0, row["not_interesting"]))
        resp.headers.update(_toast_headers("Mark as interesting first to seed this paper", "error"))
        return resp
    new_val = 0 if row["is_scout_seed"] else 1
    execute(
        "UPDATE topic_papers SET is_scout_seed = ? WHERE topic_id = ? AND paper_id = ?",
        (new_val, topic_id, paper_id),
    )
    label = "Added to scout seeds" if new_val else "Removed from scout seeds"
    resp = HTMLResponse(_tp_icons_html(topic_id, paper_id, new_val, row["not_interesting"]))
    resp.headers.update(_toast_headers(label))
    return resp


@router.post("/{topic_id}/papers/{paper_id}/toggle-not-interesting", response_class=HTMLResponse)
async def toggle_not_interesting(request: Request, topic_id: int, paper_id: int):
    row = fetch_one(
        "SELECT is_scout_seed, not_interesting FROM topic_papers WHERE topic_id = ? AND paper_id = ?",
        (topic_id, paper_id),
    )
    if not row:
        return HTMLResponse("", status_code=404)
    new_not_interesting = 0 if row["not_interesting"] else 1
    new_seed = 0 if new_not_interesting else row["is_scout_seed"]
    execute(
        "UPDATE topic_papers SET not_interesting = ?, is_scout_seed = ? WHERE topic_id = ? AND paper_id = ?",
        (new_not_interesting, new_seed, topic_id, paper_id),
    )
    label = "Marked as not interesting" if new_not_interesting else "Marked as interesting"
    resp = HTMLResponse(_tp_icons_html(topic_id, paper_id, new_seed, new_not_interesting))
    resp.headers.update(_toast_headers(label))
    return resp


# ---------- Recalculate relevance scores ----------

@router.post("/{topic_id}/recalculate-relevance", response_class=HTMLResponse)
async def recalculate_relevance(request: Request, topic_id: int):
    """Recompute relevance scores for all papers in the topic, return refreshed list."""
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    try:
        from poneglyph.services.relevance import update_topic_relevance_scores
        updated = update_topic_relevance_scores(topic_id)
    except ImportError:
        resp = HTMLResponse("")
        resp.headers["HX-Reswap"] = "none"
        resp.headers.update(_toast_headers(
            "sentence-transformers not installed — run: pip install sentence-transformers numpy",
            "error",
        ))
        return resp
    except Exception as exc:
        resp = HTMLResponse("")
        resp.headers["HX-Reswap"] = "none"
        resp.headers.update(_toast_headers(f"Relevance calculation failed: {exc}", "error"))
        return resp

    if updated == 0:
        ps = topic.get("problem_statements") or []
        msg = (
            "No problem statements defined — add some to enable relevance scoring"
            if not ps
            else "No papers found to score"
        )
        resp = HTMLResponse("")
        resp.headers["HX-Reswap"] = "none"
        resp.headers.update(_toast_headers(msg, "error"))
        return resp

    paper_rows = fetch_all(
        """SELECT p.*, tp.is_scout_seed, tp.not_interesting, tp.relevance_score
           FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           WHERE tp.topic_id = ?
           ORDER BY p.read_next DESC, tp.not_interesting ASC,
                    COALESCE(tp.relevance_score, 1.0) DESC,
                    p.published_date DESC, p.created_at DESC""",
        (topic_id,),
    )
    papers = [row_to_dict(r) for r in paper_rows]
    resp = templates.TemplateResponse(
        "topics/partials/papers_list.html",
        {"request": request, "topic": topic, "papers": papers, "show_all_link": False},
    )
    resp.headers.update(_toast_headers(f"Relevance scores updated for {updated} paper(s)"))
    return resp


# ---------- Steering log view ----------

@router.get("/{topic_id}/steering-log", response_class=HTMLResponse)
async def steering_log(request: Request, topic_id: int):
    """Return the steering log partial for HTMX lazy-load."""
    rows = fetch_all(
        """SELECT change_description, changed_at
           FROM topic_steering_log
           WHERE topic_id = ?
           ORDER BY changed_at DESC
           LIMIT 50""",
        (topic_id,),
    )
    entries = [row_to_dict(r) for r in rows]
    return templates.TemplateResponse(
        "topics/partials/steering_log.html",
        {"request": request, "entries": entries},
    )


# ---------- Steering suggestions ----------

@router.post("/{topic_id}/suggest-steering", response_class=HTMLResponse)
async def suggest_steering(request: Request, topic_id: int):
    """Run Haiku against the topic's human notes and return a suggestion form."""
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    note_rows = fetch_all(
        """SELECT p.title, pn.human_note AS note
           FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           LEFT JOIN paper_notes pn ON pn.paper_id = p.id
           WHERE tp.topic_id = ?
           ORDER BY p.published_date DESC""",
        (topic_id,),
    )
    notes = [row_to_dict(r) for r in note_rows]

    from poneglyph.services.llm_suggest import suggest_steering as _suggest
    suggestions, error = await _suggest(topic, notes)

    return templates.TemplateResponse(
        "topics/partials/steering_suggestions.html",
        {
            "request": request,
            "topic": topic,
            "suggestions": suggestions,
            "error": error,
            "note_count": sum(1 for n in notes if (n.get("note") or "").strip()),
        },
    )


@router.post("/{topic_id}/apply-suggestions", response_class=HTMLResponse)
async def apply_suggestions(request: Request, topic_id: int):
    """Apply checked steering suggestions and update the topic."""
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    form = await request.form()
    kw_add = form.getlist("kw_add")
    kw_remove = form.getlist("kw_remove")
    ps_add = form.getlist("ps_add")
    ps_remove = form.getlist("ps_remove")

    if not any([kw_add, kw_remove, ps_add, ps_remove]):
        resp = HTMLResponse("")
        resp.headers["HX-Reswap"] = "none"
        resp.headers.update(_toast_headers("No suggestions selected", "error"))
        return resp

    from poneglyph.models import _dedup_preserve_order

    old_kw = list(topic.get("keywords") or [])
    old_ps = list(topic.get("problem_statements") or [])

    kw_remove_lower = {k.lower() for k in kw_remove}
    old_kw_lower = {k.lower() for k in old_kw}
    new_kw = _dedup_preserve_order(
        [k for k in old_kw if k.lower() not in kw_remove_lower]
        + [k for k in kw_add if k.lower() not in old_kw_lower]
    )
    ps_remove_lower = {p.lower() for p in ps_remove}
    new_ps = [p for p in old_ps if p.lower() not in ps_remove_lower] + [
        p for p in ps_add if p.lower() not in {x.lower() for x in old_ps}
    ]

    execute(
        "UPDATE topics SET keywords=?, problem_statements=?, updated_at=datetime('now') WHERE id=?",
        (json.dumps(new_kw), json.dumps(new_ps), topic_id),
    )

    _log_steering_change(topic_id, topic, new_kw, list(topic.get("priority_keywords") or []), new_ps)

    # Re-score if PS changed — same guarded pattern as recalculate_relevance
    if old_ps != new_ps:
        try:
            from poneglyph.services.relevance import refresh_topic_embeddings
            topic_for_embed = {**topic, "problem_statements": new_ps}
            refresh_topic_embeddings(topic_id, topic_for_embed)
            asyncio.create_task(_run_relevance_update(topic_id))
        except Exception as exc:
            logger.warning("apply_suggestions: relevance re-score skipped: %s", exc)

    parts = []
    if kw_add:
        parts.append(f"+{len(kw_add)} keyword(s)")
    if kw_remove:
        parts.append(f"-{len(kw_remove)} keyword(s)")
    if ps_add:
        parts.append(f"+{len(ps_add)} problem statement(s)")
    if ps_remove:
        parts.append(f"-{len(ps_remove)} problem statement(s)")

    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/topics/{topic_id}"
    resp.headers.update(_toast_headers(f"Steering updated: {', '.join(parts)}"))
    return resp


# ---------- Cross-paper synthesis ----------

def _cross_status_html(run_id: int, topic_id: int) -> str:
    """Return HTMX-polling status HTML for a cross-synthesis run."""
    from poneglyph.db import fetch_one, row_to_dict
    run = row_to_dict(fetch_one("SELECT * FROM scout_runs WHERE id = ?", (run_id,)))
    if not run:
        return f'<div id="cross-status-{run_id}"><small>Run not found.</small></div>'

    status = run.get("status", "ok")
    error = run.get("error_message") or ""

    _box = (
        "background:var(--pico-card-sectioning-background-color);"
        "border-radius:0.4rem;padding:0.65rem 0.85rem;"
        "margin-top:0.75rem;font-size:0.9rem;"
    )

    if run.get("finished_at"):
        if status == "error":
            border = "border:1px solid var(--pico-del-color);"
            body = f"&#10007;&nbsp; Synthesis failed: {error}"
        else:
            border = "border:1px solid var(--pico-ins-color);"
            body = (
                "&#10003;&nbsp; Cross-paper synthesis complete. "
                f'<a href="" onclick="location.reload();return false;" '
                f'style="font-size:0.85rem;">Reload to view</a>'
            )
        return (
            f'<div id="cross-status-{run_id}" style="{_box}{border}">'
            f"{body}</div>"
        )
    else:
        border = "border:1px solid var(--pico-primary-border);"
        return (
            f'<div id="cross-status-{run_id}" style="{_box}{border}"'
            f'     hx-get="/topics/{topic_id}/cross-synthesis/status/{run_id}"'
            f"     hx-trigger=\"every 3s\""
            f'     hx-swap="outerHTML">'
            f'  <span class="spinner" style="width:0.85em;height:0.85em;'
            f'vertical-align:middle;display:inline-block;margin-right:0.4rem;"></span>'
            f"  <strong>Synthesizing…</strong> this may take 30–60 seconds."
            f"</div>"
        )


async def _run_cross_synthesis(topic_id: int, run_id: int) -> None:
    """Background task: run cross-paper synthesis and persist the result."""
    from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
    from poneglyph.pipeline import _finish_run
    from poneglyph.services.llm_cross import cross_synthesize

    try:
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        # Fetch papers + their best skim + human notes
        paper_rows = fetch_all(
            """SELECT p.*, pn.human_note
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               LEFT JOIN paper_notes pn ON pn.paper_id = p.id
               WHERE tp.topic_id = ?
               ORDER BY COALESCE(tp.relevance_score, 0.0) DESC""",
            (topic_id,),
        )

        paper_notes: list[dict] = []
        paper_ids: list[int] = []
        for row in paper_rows:
            p = row_to_dict(row)
            pid = p["id"]
            paper_ids.append(pid)

            skim_rows = fetch_all(
                """SELECT * FROM topic_paper_notes
                   WHERE topic_id = ? AND paper_id = ?
                   LIMIT 1""",
                (topic_id, pid),
            )
            skim = row_to_dict(skim_rows[0]) if skim_rows else None

            paper_notes.append({
                "paper": p,
                "skim": skim,
                "human_note": p.get("human_note"),
            })

        synthesis, directions = await cross_synthesize(topic, paper_notes)

        from poneglyph.config import settings
        execute(
            """INSERT INTO cross_syntheses
               (topic_id, paper_ids, synthesis, research_directions, model_used)
               VALUES (?, ?, ?, ?, ?)""",
            (
                topic_id,
                json.dumps(paper_ids),
                synthesis,
                json.dumps(directions),
                settings.sonnet_model,
            ),
        )
        _finish_run(run_id, found=len(paper_ids), new=1)

    except Exception as exc:
        logger.exception("cross_synthesis run %d failed: %s", run_id, exc)
        from poneglyph.pipeline import _finish_run
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc)[:500])


@router.post("/{topic_id}/cross-synthesis", response_class=HTMLResponse)
async def start_cross_synthesis(request: Request, topic_id: int):
    """Start a cross-paper synthesis run; return HTMX polling status HTML."""
    from poneglyph.pipeline import create_run

    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Topic not found.</small>')

    paper_count = fetch_one(
        "SELECT COUNT(*) as n FROM topic_papers WHERE topic_id = ?", (topic_id,)
    )
    if not paper_count or paper_count["n"] == 0:
        return HTMLResponse(
            '<small style="color:var(--pico-del-color);">No papers in topic — add papers first.</small>'
        )

    run_id = create_run(topic_id, "cross_synthesis")
    asyncio.create_task(_run_cross_synthesis(topic_id, run_id))
    return HTMLResponse(_cross_status_html(run_id, topic_id))


@router.get("/{topic_id}/cross-synthesis/status/{run_id}", response_class=HTMLResponse)
async def cross_synthesis_status(topic_id: int, run_id: int):
    """Poll endpoint for cross-synthesis run status."""
    return HTMLResponse(_cross_status_html(run_id, topic_id))


# ---------- Topic-author subscriptions ----------

@router.get("/{topic_id}/authors", response_class=HTMLResponse)
async def topic_authors_panel(request: Request, topic_id: int):
    """Return the Authors-in-scope panel partial for a topic."""
    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse("<p>Topic not found.</p>", status_code=404)

    all_authors = fetch_all("SELECT * FROM authors WHERE entity_type != 'stub' ORDER BY name", ())
    subscribed = {
        row["author_id"]
        for row in fetch_all("SELECT author_id FROM topic_authors WHERE topic_id=?", (topic_id,))
    }
    authors_with_state = []
    for row in all_authors:
        a = row_to_dict(row)
        a["subscribed"] = a["id"] in subscribed
        a["active"] = False
        if a["id"] in subscribed:
            ta = fetch_one(
                "SELECT active FROM topic_authors WHERE topic_id=? AND author_id=?",
                (topic_id, a["id"]),
            )
            a["active"] = bool(ta and ta["active"])
        authors_with_state.append(a)

    return templates.TemplateResponse(
        "topics/partials/topic_authors.html",
        {"request": request, "topic": topic, "authors": authors_with_state},
    )


@router.post("/{topic_id}/authors/{author_id}/toggle", response_class=HTMLResponse)
async def toggle_topic_author(request: Request, topic_id: int, author_id: int):
    """Toggle an author's subscription for a topic; trigger backfill on first opt-in."""
    existing = fetch_one(
        "SELECT active FROM topic_authors WHERE topic_id=? AND author_id=?",
        (topic_id, author_id),
    )

    if existing is None:
        # First opt-in — create subscription and trigger backfill
        execute(
            "INSERT OR IGNORE INTO topic_authors (topic_id, author_id, active) VALUES (?, ?, 1)",
            (topic_id, author_id),
        )
        execute(
            "INSERT INTO topic_steering_log (topic_id, change_description) VALUES (?, ?)",
            (topic_id, f"Subscribed to author id={author_id}; backfill queued"),
        )
        run_id = execute(
            "INSERT INTO scout_runs (topic_id, source) VALUES (?, 'article_scout_backfill')",
            (topic_id,),
        )
        from datetime import datetime, timedelta, timezone
        from poneglyph.pipeline import run_article_scout_for_topic
        lookback_row = fetch_one(
            "SELECT scout_lookback_days FROM topic_authors WHERE topic_id=? AND author_id=?",
            (topic_id, author_id),
        )
        lookback = (lookback_row["scout_lookback_days"] if lookback_row else 30) or 30
        since = datetime.now(timezone.utc) - timedelta(days=lookback)
        asyncio.create_task(run_article_scout_for_topic(topic_id, run_id, since=since))
        new_active = True
    else:
        new_active = not existing["active"]
        execute(
            "UPDATE topic_authors SET active=? WHERE topic_id=? AND author_id=?",
            (int(new_active), topic_id, author_id),
        )

    return await topic_authors_panel(request, topic_id)


@router.post("/{topic_id}/scout-articles", response_class=HTMLResponse)
async def start_article_scout(request: Request, topic_id: int):
    """Start an on-demand article scout for a topic."""
    from poneglyph.pipeline import create_run, run_article_scout_for_topic

    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Topic not found.</small>')

    sub_count = fetch_one(
        "SELECT COUNT(*) as n FROM topic_authors WHERE topic_id=? AND active=1", (topic_id,)
    )
    if not sub_count or sub_count["n"] == 0:
        return HTMLResponse(
            '<small style="color:var(--pico-del-color);">No active author subscriptions — '
            'add authors to this topic first.</small>'
        )

    run_id = create_run(topic_id, "article_scout")
    asyncio.create_task(run_article_scout_for_topic(topic_id, run_id))

    from poneglyph.routes.scout import _run_status_html
    return HTMLResponse(_run_status_html(run_id))


@router.post("/{topic_id}/scout-now", response_class=HTMLResponse)
async def start_scout_now(request: Request, topic_id: int):
    """Start citation scout and article scout in parallel; skip whichever leg lacks prerequisites."""
    from poneglyph.pipeline import create_run, run_topic_scout, run_article_scout_for_topic
    from poneglyph.routes.scout import _run_status_html

    topic = _topic_row(topic_id)
    if not topic:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Topic not found.</small>')

    html_parts: list[str] = []
    _label = (
        'style="font-size:0.85rem;font-weight:600;margin:0.25rem 0 0.1rem;"'
    )

    # --- Citation scout leg ---
    if topic.get("skim_skill_md"):
        seed_row = fetch_one(
            "SELECT COUNT(*) as n FROM topic_papers WHERE topic_id=? AND is_scout_seed=1",
            (topic_id,),
        )
        if seed_row and seed_row["n"] > 0:
            cit_run_id = create_run(topic_id, "topic_scout")
            asyncio.create_task(run_topic_scout(topic_id, cit_run_id))
            html_parts.append(f"<p {_label}>Citation Scout</p>" + _run_status_html(cit_run_id))
        else:
            html_parts.append(
                '<small style="color:var(--pico-muted-color);">Citation scout: no seed papers — skipped.</small>'
            )
    else:
        html_parts.append(
            '<small style="color:var(--pico-muted-color);">Citation scout: no skim skill — skipped.</small>'
        )

    # --- Article scout leg ---
    sub_row = fetch_one(
        "SELECT COUNT(*) as n FROM topic_authors WHERE topic_id=? AND active=1", (topic_id,)
    )
    if sub_row and sub_row["n"] > 0:
        art_run_id = create_run(topic_id, "article_scout")
        asyncio.create_task(run_article_scout_for_topic(topic_id, art_run_id))
        html_parts.append(
            f'<p {_label} style="margin-top:0.75rem;">Article Scout</p>'
            + _run_status_html(art_run_id)
        )
    else:
        html_parts.append(
            '<small style="color:var(--pico-muted-color);display:block;margin-top:0.5rem;">'
            "Article scout: no active author subscriptions — skipped.</small>"
        )

    return HTMLResponse("\n".join(html_parts))


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
