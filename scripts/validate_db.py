"""Validate the live DB. Exit code is non-zero on any failure.

Designed to run before backup_db.py — Task Scheduler should chain them so
a corrupt DB never overwrites a good backup.

Checks:
  - PRAGMA integrity_check == 'ok'
  - PRAGMA foreign_key_check returns no rows
  - For every paper.pdf_local_path that's set, the file exists on disk
  - For every topic_paper_notes row with deep_synthesis NOT NULL, the
    matching topic_papers link still exists

Usage: python scripts/validate_db.py
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poneglyph.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate_db")


def main() -> int:
    db_path = Path(settings.database_path)
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    failures = 0
    try:
        # 1. Integrity check
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            logger.error("integrity_check FAILED: %s", result)
            failures += 1
        else:
            logger.info("integrity_check: ok")

        # 2. FK check
        bad_fks = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad_fks:
            logger.error("foreign_key_check found %d violations", len(bad_fks))
            for row in bad_fks[:10]:
                logger.error("  %s", tuple(row))
            failures += 1
        else:
            logger.info("foreign_key_check: ok")

        # 3. PDF existence
        missing_pdfs = []
        for row in conn.execute(
            "SELECT id, title, pdf_local_path FROM papers "
            "WHERE pdf_local_path IS NOT NULL AND pdf_local_path != ''"
        ):
            p = row["pdf_local_path"]
            if not p.lower().startswith(("http://", "https://")):
                if not Path(p).exists():
                    missing_pdfs.append((row["id"], row["title"], p))
        if missing_pdfs:
            logger.warning("Missing PDF files: %d", len(missing_pdfs))
            for pid, title, path in missing_pdfs[:10]:
                logger.warning("  paper=%d  %s  ->  %s", pid, title[:60], path)
        else:
            logger.info("PDF existence: all %d local PDFs found",
                        conn.execute(
                            "SELECT COUNT(*) FROM papers WHERE pdf_local_path IS NOT NULL "
                            "AND pdf_local_path != '' AND pdf_local_path NOT LIKE 'http%'"
                        ).fetchone()[0])

        # 4. Orphan deep syntheses
        orphans = conn.execute(
            """SELECT COUNT(*) FROM topic_paper_notes tpn
               WHERE tpn.deep_synthesis IS NOT NULL AND tpn.deep_synthesis != ''
                 AND NOT EXISTS (
                     SELECT 1 FROM topic_papers tp
                     WHERE tp.topic_id = tpn.topic_id AND tp.paper_id = tpn.paper_id
                 )"""
        ).fetchone()[0]
        if orphans:
            logger.error("Found %d topic_paper_notes with deep_synthesis but no topic_papers link", orphans)
            failures += 1
        else:
            logger.info("deep_synthesis ↔ topic_papers consistency: ok")
    finally:
        conn.close()

    if failures:
        logger.error("validate_db: %d check(s) failed", failures)
        return 2
    logger.info("validate_db: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
