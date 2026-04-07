"""Shared Jinja2 templates instance to avoid circular imports."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

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


# Register custom filters
templates.env.filters["author_cite"] = _author_cite
