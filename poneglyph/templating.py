"""Shared Jinja2 templates instance to avoid circular imports."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

_NYC = ZoneInfo("America/New_York")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _author_cite(authors: list[str] | str | None, year: str | None = None) -> str:
    """Format authors as 'A, B and C (2022)' or 'A, B and C et al (2022)' for >3 authors."""
    if not authors:
        return ""
    if isinstance(authors, str):
        authors = [authors]
    # Extract year (4-digit) from date string if provided
    yr = ""
    if year:
        for part in year.replace("-", " ").split():
            if len(part) == 4 and part.isdigit():
                yr = part
                break
    # Format author list — use last names only if "Firstname Lastname" format
    names = []
    for a in authors:
        a = a.strip()
        if not a:
            continue
        parts = a.split()
        if len(parts) >= 2 and not any(c.isdigit() for c in a):
            names.append(parts[-1])  # last name
        else:
            names.append(a)  # institution or single name

    if not names:
        return ""
    if len(names) == 1:
        citation = names[0]
    elif len(names) == 2:
        citation = f"{names[0]} and {names[1]}"
    elif len(names) == 3:
        citation = f"{names[0]}, {names[1]} and {names[2]}"
    else:
        citation = f"{names[0]}, {names[1]} and {names[2]} et al"

    if yr:
        citation += f" ({yr})"
    return citation


def _is_url(value: str | None) -> bool:
    """Return True if the value looks like an http/https URL rather than a file path."""
    if not value:
        return False
    v = value.strip()
    return v.startswith("http://") or v.startswith("https://")


def _nyc_time(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a UTC datetime string (from SQLite) to NYC local time."""
    if not value:
        return ""
    try:
        # SQLite datetime('now') → "YYYY-MM-DD HH:MM:SS"
        dt_utc = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        dt_nyc = dt_utc.astimezone(_NYC)
        return dt_nyc.strftime(fmt)
    except (ValueError, TypeError):
        return str(value)


def _md_to_html(value: str | None) -> str:
    """Convert Markdown text to HTML. Returns empty string for None/empty input.
    Falls back to <pre>-escaped text if the markdown package is not installed.
    """
    if not value:
        return ""
    try:
        import markdown as _md
        return _md.markdown(value, extensions=["extra", "nl2br", "sane_lists"])
    except ImportError:
        import html as _html
        return f"<pre style='white-space:pre-wrap'>{_html.escape(value)}</pre>"


# Register custom filters and globals
templates.env.filters["author_cite"] = _author_cite
templates.env.filters["nyc"] = _nyc_time
templates.env.filters["md"] = _md_to_html
templates.env.globals["is_url"] = _is_url
