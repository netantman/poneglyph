"""Authors CRUD routes — global library of authors/aggregators to scout."""

import json
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict, transaction
from poneglyph.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/authors", tags=["authors"])


def _toast(message: str, toast_type: str = "success") -> dict:
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": toast_type}})}


def _author_row(author_id: int) -> dict | None:
    author = row_to_dict(fetch_one("SELECT * FROM authors WHERE id = ?", (author_id,)))
    if not author:
        return None
    sources = [
        row_to_dict(r)
        for r in fetch_all("SELECT * FROM author_sources WHERE author_id = ? ORDER BY id", (author_id,))
    ]
    author["sources"] = sources
    return author


def _all_authors() -> list[dict]:
    rows = fetch_all("SELECT * FROM authors ORDER BY entity_type, name")
    result = []
    for row in rows:
        a = row_to_dict(row)
        sources = [
            row_to_dict(r)
            for r in fetch_all(
                "SELECT * FROM author_sources WHERE author_id = ? ORDER BY id", (a["id"],)
            )
        ]
        a["sources"] = sources
        result.append(a)
    return result


# ---------- List ----------

@router.get("", response_class=HTMLResponse)
async def list_authors(request: Request):
    authors = _all_authors()
    stubs = [a for a in authors if a.get("entity_type") == "stub"]
    non_stubs = [a for a in authors if a.get("entity_type") != "stub"]
    return templates.TemplateResponse(
        "authors/list.html",
        {"request": request, "authors": non_stubs, "stubs": stubs},
    )


# ---------- Suggest URL (HTMX) ----------

@router.get("/suggest-url", response_class=HTMLResponse)
async def suggest_url(request: Request, name: str = "", hint: str = ""):
    """Return a pre-filled URL suggestion for the add-author form."""
    name = name.strip()
    if not name:
        return HTMLResponse("")

    from poneglyph.services.llm_suggest_author_source import suggest_source_url

    url, entity_type, reason = await suggest_source_url(name, hint)
    if not url:
        return HTMLResponse(
            f'<div style="min-height:1.2rem; margin-bottom:0.25rem; font-size:0.85rem;">'
            f'<small style="color:var(--pico-muted-color);">Could not suggest a URL — paste one manually.</small>'
            f'</div>'
            f'<label>RSS / Feed URL'
            f'<input type="url" name="source_url" id="new-source-url"'
            f'       placeholder="https://example.substack.com/feed">'
            f'</label>'
        )
    escaped_url = url.replace('"', "&quot;")
    return HTMLResponse(
        f'<div style="min-height:1.2rem; margin-bottom:0.25rem; font-size:0.85rem;">'
        f'<small style="color:#d97706;">&#9888; LLM guess — verify before adding ({reason})</small>'
        f'</div>'
        f'<label>RSS / Feed URL'
        f'<input type="url" name="source_url" id="new-source-url"'
        f'       value="{escaped_url}"'
        f'       placeholder="https://example.substack.com/feed"'
        f'       style="border-color:#d97706;"'
        f'       oninput="this.style.borderColor=\'\'">'
        f'</label>'
    )


# ---------- Create author ----------

@router.post("", response_class=HTMLResponse)
async def create_author(
    request: Request,
    name: str = Form(...),
    byline: str = Form(""),
    entity_type: str = Form("author"),
    notes: str = Form(""),
    source_url: str = Form(""),
    source_type: str = Form("rss"),
):
    name = name.strip()
    source_url = source_url.strip()

    if entity_type not in ("author", "aggregator", "stub"):
        entity_type = "author"

    # Upsert author (no upfront RSS verification — bad URLs surface naturally during scouting)
    try:
        author_id = execute(
            """INSERT INTO authors (name, byline, entity_type, source_origin, notes)
               VALUES (?, ?, ?, 'manual', ?)
               ON CONFLICT(name) DO UPDATE SET
                 byline=excluded.byline, entity_type=excluded.entity_type,
                 notes=excluded.notes""",
            (name, byline.strip(), entity_type, notes.strip()),
        )
        if not author_id:
            row = fetch_one("SELECT id FROM authors WHERE name = ?", (name,))
            author_id = row["id"] if row else None
    except Exception as exc:
        logger.warning("create_author: insert failed: %s", exc)
        resp = HTMLResponse(f'<p style="color:var(--pico-del-color);">Error: {exc}</p>')
        return resp

    if source_url and author_id:
        import datetime

        execute(
            """INSERT OR IGNORE INTO author_sources
               (author_id, source_type, url, verified_at, last_status)
               VALUES (?, ?, ?, NULL, 'unverified')""",
            (author_id, source_type, source_url),
        )

    if request.headers.get("HX-Request"):
        resp = HTMLResponse("")
        resp.headers["HX-Redirect"] = "/authors"
        resp.headers.update(_toast(f"Author '{name}' added"))
        return resp

    return RedirectResponse("/authors", status_code=303)


# ---------- Delete author ----------

@router.delete("/{author_id}", response_class=HTMLResponse)
async def delete_author(request: Request, author_id: int):
    author = row_to_dict(fetch_one("SELECT name FROM authors WHERE id = ?", (author_id,)))
    name = author["name"] if author else "Author"
    execute("DELETE FROM authors WHERE id = ?", (author_id,))
    authors = _all_authors()
    stubs = [a for a in authors if a.get("entity_type") == "stub"]
    non_stubs = [a for a in authors if a.get("entity_type") != "stub"]
    resp = templates.TemplateResponse(
        "authors/list.html",
        {"request": request, "authors": non_stubs, "stubs": stubs},
    )
    resp.headers.update(_toast(f"'{name}' deleted"))
    return resp


# ---------- Edit author ----------

@router.get("/{author_id}/edit", response_class=HTMLResponse)
async def edit_author_form(request: Request, author_id: int):
    author = _author_row(author_id)
    if not author:
        return HTMLResponse("<p>Author not found.</p>", status_code=404)
    return templates.TemplateResponse(
        "authors/partials/edit_form.html",
        {"request": request, "author": author},
    )


@router.put("/{author_id}", response_class=HTMLResponse)
async def update_author(
    request: Request,
    author_id: int,
    name: str = Form(...),
    byline: str = Form(""),
    entity_type: str = Form("author"),
    notes: str = Form(""),
    source_url: str = Form(""),
    source_type: str = Form("rss"),
):
    if entity_type not in ("author", "aggregator", "stub"):
        entity_type = "author"
    execute(
        "UPDATE authors SET name=?, byline=?, entity_type=?, notes=? WHERE id=?",
        (name.strip(), byline.strip(), entity_type, notes.strip(), author_id),
    )
    if entity_type != "stub":
        execute(
            "UPDATE authors SET source_origin='manual' WHERE id=? AND source_origin='aggregator_dereference'",
            (author_id,),
        )
    if source_url.strip():
        execute(
            """INSERT OR IGNORE INTO author_sources
               (author_id, source_type, url, verified_at, last_status)
               VALUES (?, ?, ?, NULL, 'unverified')""",
            (author_id, source_type, source_url.strip()),
        )
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/authors"
    resp.headers.update(_toast(f"'{name.strip()}' updated"))
    return resp


# ---------- Add source to author ----------

@router.post("/{author_id}/sources", response_class=HTMLResponse)
async def add_source(
    request: Request,
    author_id: int,
    url: str = Form(...),
    source_type: str = Form("rss"),
):
    url = url.strip()
    author = row_to_dict(fetch_one("SELECT * FROM authors WHERE id = ?", (author_id,)))
    if not author:
        return HTMLResponse("<p>Author not found.</p>", status_code=404)

    from poneglyph.services.rss_fetch import verify_feed
    import datetime

    ok, err = await verify_feed(url)
    if not ok:
        resp = HTMLResponse(
            f'<small style="color:var(--pico-del-color);">Verification failed: {err}</small>'
        )
        return resp

    execute(
        """INSERT OR IGNORE INTO author_sources
           (author_id, source_type, url, verified_at, last_status)
           VALUES (?, ?, ?, ?, 'ok')""",
        (author_id, source_type, url, datetime.datetime.utcnow().isoformat()),
    )
    author_data = _author_row(author_id)
    resp = templates.TemplateResponse(
        "authors/partials/sources_list.html",
        {"request": request, "author": author_data},
    )
    resp.headers.update(_toast("Source added"))
    return resp


# ---------- Test feed ----------

@router.get("/{author_id}/sources/{source_id}/test", response_class=HTMLResponse)
async def test_feed(request: Request, author_id: int, source_id: int):
    src = row_to_dict(fetch_one(
        "SELECT * FROM author_sources WHERE id = ? AND author_id = ?", (source_id, author_id)
    ))
    if not src:
        return HTMLResponse('<small style="color:var(--pico-del-color);">Source not found.</small>')

    from poneglyph.services.rss_fetch import fetch_feed
    result = await fetch_feed(src["url"])

    if result.error:
        execute(
            "UPDATE author_sources SET last_status=?, last_error=? WHERE id=?",
            ("fetch_error", result.error[:500], source_id),
        )
        src["last_status"] = "fetch_error"
        test_results_html = (
            f'<small style="color:var(--pico-del-color);">&#10007; Error: {result.error}</small>'
        )
    else:
        execute(
            "UPDATE author_sources SET last_status='ok', last_error=NULL WHERE id=?",
            (source_id,),
        )
        src["last_status"] = "ok"
        items = result.items[:5]
        row_parts = []
        for item in items:
            if item.published_dt:
                date_span = (
                    '<span style="color:var(--pico-muted-color); '
                    'font-size:0.72rem; margin-left:0.4rem;">'
                    f'{str(item.published_dt)[:10]}</span>'
                )
            else:
                date_span = ""
            title = item.title or "(no title)"
            row_parts.append(
                '<div style="padding:0.15rem 0; '
                'border-bottom:1px solid var(--pico-muted-border-color);">'
                f'<a href="{item.link}" target="_blank" rel="noopener" '
                f'style="font-size:0.78rem;">{title}</a>'
                f'{date_span}'
                '</div>'
            )
        rows = "".join(row_parts)
        total = len(result.items)
        test_results_html = (
            f'<div style="margin-top:0.3rem; padding:0.4rem 0.6rem; '
            f'background:var(--pico-card-sectioning-background-color); border-radius:0.35rem;">'
            f'<small style="color:#16a34a;">&#10003; Feed OK — {total} item{"s" if total != 1 else ""}</small>'
            f'{rows}'
            f'</div>'
        )

    return templates.TemplateResponse(
        request,
        "authors/partials/source_entry.html",
        {"src": src, "test_results_html": test_results_html},
    )


# ---------- Delete source ----------

@router.delete("/{author_id}/sources/{source_id}", response_class=HTMLResponse)
async def delete_source(request: Request, author_id: int, source_id: int):
    execute("DELETE FROM author_sources WHERE id = ? AND author_id = ?", (source_id, author_id))
    author_data = _author_row(author_id)
    # If the request came from the edit form it targets the author row, so
    # return the full edit form so the row stays intact.
    hx_target = request.headers.get("HX-Target", "")
    template = (
        "authors/partials/edit_form.html"
        if hx_target == f"author-row-{author_id}"
        else "authors/partials/sources_list.html"
    )
    resp = templates.TemplateResponse(template, {"request": request, "author": author_data})
    resp.headers.update(_toast("Source removed"))
    return resp


# ---------- Promote stub ----------

@router.post("/{author_id}/promote", response_class=HTMLResponse)
async def promote_stub(
    request: Request,
    author_id: int,
    entity_type: str = Form("author"),
):
    """Promote a stub author to a real author or aggregator."""
    if entity_type not in ("author", "aggregator"):
        entity_type = "author"
    execute(
        "UPDATE authors SET entity_type=?, source_origin='manual' WHERE id=?",
        (entity_type, author_id),
    )
    authors = _all_authors()
    stubs = [a for a in authors if a.get("entity_type") == "stub"]
    non_stubs = [a for a in authors if a.get("entity_type") != "stub"]
    resp = templates.TemplateResponse(
        "authors/list.html",
        {"request": request, "authors": non_stubs, "stubs": stubs},
    )
    resp.headers.update(_toast("Author promoted"))
    return resp
