"""PDF file management: naming, saving, moving across subfolders."""

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

from poneglyph.config import settings


def get_pdf_base_dir() -> Path:
    return Path(settings.pdf_base_dir)


def list_subfolders() -> list[str]:
    """Return sorted list of top-level subfolder names under the PDF base directory."""
    base = get_pdf_base_dir()
    if not base.exists():
        return []
    return sorted([d.name for d in base.iterdir() if d.is_dir()])


def list_all_pdf_files() -> list[str]:
    """Return sorted list of all PDF relative paths under the base directory (recursive).

    Paths use forward slashes, e.g. 'Research/Quant/paper.pdf'.
    """
    base = get_pdf_base_dir()
    if not base.exists():
        return []
    return sorted(
        [p.relative_to(base).as_posix() for p in base.rglob("*.pdf")],
        key=str.lower,
    )


def _sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip().strip(".")[:200]


_ACADEMIC_NAMING_SUBFOLDERS = {"Public-Academia"}


def _academic_naming_subfolders() -> set[str]:
    """Subfolders that use the Author1, Author2, ..., (year), Title.pdf convention."""
    return _ACADEMIC_NAMING_SUBFOLDERS | {settings.pdf_scouting_subfolder}


def _last_name(full_name: str) -> str:
    """Extract last name from a full name string."""
    parts = full_name.strip().split()
    return parts[-1] if parts else full_name.strip()


def build_pdf_filename(subfolder: str, title: str, authors: list[str] | None = None,
                       year: str | None = None) -> str:
    """Build the PDF filename based on subfolder conventions.

    Public-Academia and scouting subfolder:
        'LastName1, LastName2, ..., (year), Title.pdf'  (last names alphabetically sorted)
    All others: 'Title.pdf'
    """
    if subfolder in _academic_naming_subfolders() and authors:
        last_names = sorted(_last_name(a) for a in authors if a.strip())
        if len(last_names) >= 2:
            author_str = ", ".join(last_names[:-1]) + " and " + last_names[-1]
        else:
            author_str = last_names[0] if last_names else ""
        year_str = f"({year})" if year else "(n.d.)"
        raw = f"{author_str} {year_str}, {title}"
    else:
        raw = title
    return _sanitize_filename(raw) + ".pdf"


def list_pdf_files(subfolder: str) -> list[str]:
    """Return sorted list of PDF filenames in the given subfolder."""
    subfolder_path = get_pdf_base_dir() / subfolder
    if not subfolder_path.exists() or not subfolder_path.is_dir():
        return []
    return sorted([p.name for p in subfolder_path.glob("*.pdf")], key=str.lower)


def save_pdf(content: bytes, subfolder: str, filename: str) -> Path:
    """Save PDF bytes to the given subfolder. Returns the full path."""
    dest_dir = get_pdf_base_dir() / subfolder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(content)
    return dest


def move_pdf(current_path: Path, new_subfolder: str, new_filename: str) -> Path:
    """Move a PDF to a new subfolder with a new filename. Returns the new path."""
    new_dir = get_pdf_base_dir() / new_subfolder
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / new_filename
    if current_path != new_path:
        shutil.move(str(current_path), str(new_path))
    return new_path


def extract_pdf_text(path: Path, max_pages: int = 5) -> str:
    """Extract text from the first max_pages pages of a PDF. Returns empty string on failure."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        texts = []
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ""
            texts.append(text)
        return "\n\n".join(texts).strip()
    except Exception as exc:
        logger.warning("extract_pdf_text failed for %s: %s", path, exc)
        return ""


def copy_to_working_papers(pdf_path: Path, filename: str) -> Path:
    """Copy PDF to ~/Desktop/poneglyph_working_papers/."""
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "Desktop"
    wp_dir = desktop / "poneglyph_working_papers"
    wp_dir.mkdir(parents=True, exist_ok=True)
    dest = wp_dir / filename
    shutil.copy2(pdf_path, dest)
    return dest
