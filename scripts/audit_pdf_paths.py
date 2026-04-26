"""Audit papers.pdf_local_path against what's actually on disk.

Reports:
  - Papers with pdf_local_path set but the file is missing
  - Papers whose path looks like a OneDrive sync conflict (`paper (1).pdf`)
  - Files in the PDF base dir that aren't referenced by any paper row

Read-only: writes nothing. Use the output to manually fix paths in the UI.

Usage: python scripts/audit_pdf_paths.py
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poneglyph.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_pdf")

# matches "paper (1).pdf", "paper-Copy.pdf", "...-conflict.pdf"
_CONFLICT_RE = re.compile(r"\(\d+\)\.pdf$|-Copy\.pdf$|-conflict\.pdf$", re.IGNORECASE)


def main() -> int:
    conn = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    referenced: set[Path] = set()
    missing: list[tuple[int, str, str]] = []
    conflicts: list[tuple[int, str, str]] = []

    try:
        for row in conn.execute(
            "SELECT id, title, pdf_local_path FROM papers "
            "WHERE pdf_local_path IS NOT NULL AND pdf_local_path != ''"
        ):
            p = row["pdf_local_path"]
            if p.lower().startswith(("http://", "https://")):
                continue
            path = Path(p)
            referenced.add(path.resolve())
            if not path.exists():
                missing.append((row["id"], row["title"], p))
            if _CONFLICT_RE.search(path.name):
                conflicts.append((row["id"], row["title"], p))
    finally:
        conn.close()

    base = Path(settings.pdf_base_dir)
    on_disk: set[Path] = set()
    if base.exists():
        on_disk = {p.resolve() for p in base.rglob("*.pdf")}
    orphans = sorted(on_disk - referenced)

    print("=" * 70)
    print(f"Missing files referenced by DB: {len(missing)}")
    for pid, title, path in missing:
        print(f"  paper={pid}  {title[:60]}\n      -> {path}")
    print()
    print(f"Sync-conflict-looking filenames: {len(conflicts)}")
    for pid, title, path in conflicts:
        print(f"  paper={pid}  {title[:60]}\n      -> {path}")
    print()
    print(f"PDFs on disk not referenced by any paper row: {len(orphans)}")
    for p in orphans[:50]:
        print(f"  {p}")
    if len(orphans) > 50:
        print(f"  ... and {len(orphans) - 50} more")
    print("=" * 70)

    return 0 if not (missing or conflicts) else 2


if __name__ == "__main__":
    sys.exit(main())
