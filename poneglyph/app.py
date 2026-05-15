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
