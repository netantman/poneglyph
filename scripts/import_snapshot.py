"""Rebuild a SQLite DB from a JSONL snapshot directory written by export_snapshot.py.

Usage: python scripts/import_snapshot.py <snapshot_dir> <target_db_path>

The target DB is created fresh (overwritten if it exists). Schema is built by
poneglyph.db.init_db() against the target path before rows are inserted.
Embeddings are NOT restored (snapshot doesn't include them); regenerate via
the existing embedding-rebuild flow if needed.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("import_snapshot")

# Restore order matters because of foreign keys.
TABLE_ORDER = [
    "topics",
    "papers",
    "topic_papers",
    "paper_citations",
    "paper_notes",
    "topic_paper_notes",
    "cross_syntheses",
    "scout_runs",
    "topic_steering_log",
    "qa_history",
]


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 64

    snapshot_dir = Path(sys.argv[1]).resolve()
    target_db = Path(sys.argv[2]).resolve()
    if not snapshot_dir.is_dir():
        logger.error("Snapshot dir not found: %s", snapshot_dir)
        return 1

    if target_db.exists():
        logger.warning("Target DB exists, overwriting: %s", target_db)
        target_db.unlink()

    # Build empty schema at the target path by pointing the app's settings there.
    os.environ["DATABASE_PATH"] = str(target_db)
    from poneglyph.config import settings  # noqa: E402
    settings.database_path = str(target_db)
    from poneglyph.db import init_db  # noqa: E402
    init_db()

    conn = sqlite3.connect(str(target_db))
    conn.execute("PRAGMA foreign_keys=OFF")  # bulk insert; schema already enforces shape
    try:
        for table in TABLE_ORDER:
            rows = _load_jsonl(snapshot_dir / f"{table}.jsonl")
            if not rows:
                logger.info("%s: no rows in snapshot, skipping", table)
                continue
            cols = list(rows[0].keys())
            placeholders = ", ".join(["?"] * len(cols))
            collist = ", ".join(cols)
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
            logger.info("%s: restored %d rows", table, len(rows))
        conn.commit()
        # Re-enable FKs and run a check
        conn.execute("PRAGMA foreign_keys=ON")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            logger.error("foreign_key_check found %d violations after import", len(bad))
            for row in bad[:10]:
                logger.error("  %s", tuple(row))
        else:
            logger.info("foreign_key_check: ok")
    finally:
        conn.close()

    logger.info("Restored snapshot %s -> %s", snapshot_dir, target_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
