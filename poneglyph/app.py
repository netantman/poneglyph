"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from poneglyph.db import init_db
from poneglyph.templating import templates

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Poneglyph", version="0.1.0")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    _recover_stale_pending_skims()


def _recover_stale_pending_skims() -> None:
    """On startup, re-queue any pending_skims rows stuck in 'pending' from a prior crash.

    Rows older than 5 minutes are considered stale (the server restarted mid-drain).
    We reset them to 'pending' so the next Scout Now will pick them up again.
    Rows younger than 5 minutes are left alone — an in-progress drain may have just started.
    """
    import logging
    from poneglyph.db import execute, fetch_one

    logger = logging.getLogger(__name__)
    try:
        result = fetch_one(
            """SELECT COUNT(*) as n FROM pending_skims
               WHERE status = 'pending'
                 AND queued_at < datetime('now', '-5 minutes')""",
        )
        stale = result["n"] if result else 0
        if stale:
            execute(
                """UPDATE pending_skims SET status = 'pending'
                   WHERE status = 'pending'
                     AND queued_at < datetime('now', '-5 minutes')""",
            )
            logger.info("startup: reset %d stale pending_skims rows", stale)
    except Exception as exc:
        logging.getLogger(__name__).warning("startup: pending_skims recovery failed: %s", exc)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- Register route modules ---
from poneglyph.routes import authors, papers, scout, topics, search  # noqa: E402

app.include_router(topics.router)
app.include_router(papers.router)
app.include_router(scout.router)
app.include_router(search.router)
app.include_router(authors.router)
