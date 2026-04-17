"""Scouting endpoints — start runs, poll status."""

import asyncio
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from poneglyph.db import fetch_one, row_to_dict
from poneglyph.pipeline import create_run, run_paper_enrichment, run_topic_scout
from poneglyph.templating import templates

router = APIRouter(prefix="/scout", tags=["scout"])


# ---------- Status HTML helpers ----------

def _run_status_html(run_id: int) -> str:
    run = row_to_dict(fetch_one("SELECT * FROM scout_runs WHERE id = ?", (run_id,)))
    if not run:
        return f'<div id="scout-status-{run_id}"><small>Run not found.</small></div>'

    status = run.get("status", "ok")
    found = run.get("papers_found", 0)
    new = run.get("papers_new", 0)
    error = run.get("error_message") or ""

    _box = (
        "background:var(--pico-card-sectioning-background-color);"
        "border-radius:0.4rem;padding:0.65rem 0.85rem;"
        "margin-top:0.75rem;font-size:0.9rem;"
    )

    if run.get("finished_at"):
        # Done — static box, no more polling
        if status == "error":
            border = "border:1px solid var(--pico-del-color);"
            body = f'&#10007;&nbsp; Scout failed: {error}'
        else:
            border = "border:1px solid var(--pico-ins-color);"
            body = (
                f'&#10003;&nbsp; Scouting complete — '
                f'<strong>{found}</strong> papers discovered, '
                f'<strong>{new}</strong> synthesized. '
                f'<a href="" onclick="location.reload();return false;" style="font-size:0.85rem;">Reload page</a>'
            )
        return (
            f'<div id="scout-status-{run_id}" style="{_box}{border}">'
            f'{body}</div>'
        )
    else:
        # Still running — HTMX polls every 3s
        border = "border:1px solid var(--pico-primary-border);"
        return (
            f'<div id="scout-status-{run_id}" style="{_box}{border}"'
            f'     hx-get="/scout/run/{run_id}"'
            f'     hx-trigger="every 3s"'
            f'     hx-swap="outerHTML">'
            f'  <span class="spinner" style="width:0.85em;height:0.85em;vertical-align:middle;display:inline-block;margin-right:0.4rem;"></span>'
            f'  <strong>Scouting in progress</strong> — {found} paper{"s" if found != 1 else ""} discovered so far…'
            f'</div>'
        )


# ---------- Poll route ----------

@router.get("/run/{run_id}", response_class=HTMLResponse)
async def get_run_status(run_id: int):
    return HTMLResponse(_run_status_html(run_id))


# ---------- Start topic scout ----------

@router.post("/topic/{topic_id}", response_class=HTMLResponse)
async def start_topic_scout(request: Request, topic_id: int):
    topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
    if not topic:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Topic not found.</small>')

    paper_count = fetch_one(
        "SELECT COUNT(*) as n FROM topic_papers WHERE topic_id = ?", (topic_id,)
    )
    if not paper_count or paper_count["n"] == 0:
        return HTMLResponse(
            '<small style="color:var(--pico-del-color);">No papers in topic — '
            'add at least one paper before scouting.</small>'
        )

    run_id = create_run(topic_id, "topic_scout")
    asyncio.create_task(run_topic_scout(topic_id, run_id))
    return HTMLResponse(_run_status_html(run_id))


# ---------- Start paper enrichment ----------

@router.post("/paper/{paper_id}", response_class=HTMLResponse)
async def start_paper_enrichment(request: Request, paper_id: int, topic_id: int = Form(...)):
    paper = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))
    if not paper:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Paper not found.</small>')

    run_id = create_run(topic_id, "paper_enrichment")
    asyncio.create_task(run_paper_enrichment(paper_id, topic_id, run_id))
    return HTMLResponse(_run_status_html(run_id))
