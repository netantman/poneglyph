"""Export every table to JSONL + dump skill prompts as standalone .md files.

Format-portability hedge against `.db` corruption. Skips embeddings (rebuildable).

Output: <PONEGLYPH_BACKUP_DIR>/snapshot-YYYYMMDD/
  ├── topics.jsonl
  ├── papers.jsonl
  ├── ... (one file per table)
  └── skills/
      ├── <topic_name>.skim_skill.md
      └── <topic_name>.deep_synthesis_skill.md

Run: python scripts/export_snapshot.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poneglyph.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("export_snapshot")

DEFAULT_BACKUP_DIR = Path(
    r"C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups"
)
SKIP_TABLES = {"paper_embeddings", "topic_embeddings", "papers_fts",
               "papers_fts_data", "papers_fts_idx", "papers_fts_docsize",
               "papers_fts_config"}


def _backup_dir() -> Path:
    override = os.environ.get("PONEGLYPH_BACKUP_DIR")
    return Path(override) if override else DEFAULT_BACKUP_DIR


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_") or "untitled"


def main() -> int:
    src = Path(settings.database_path)
    if not src.exists():
        logger.error("Live DB not found at %s", src)
        return 1

    out_root = _backup_dir() / f"snapshot-{datetime.now():%Y%m%d}"
    out_root.mkdir(parents=True, exist_ok=True)
    skills_dir = out_root / "skills"
    skills_dir.mkdir(exist_ok=True)

    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if r[0] not in SKIP_TABLES
        ]
        for table in tables:
            out_path = out_root / f"{table}.jsonl"
            count = 0
            with out_path.open("w", encoding="utf-8") as f:
                for row in conn.execute(f"SELECT * FROM {table}"):
                    f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    count += 1
            logger.info("%s -> %s (%d rows)", table, out_path.name, count)

        # Skill prompts as standalone Markdown for human-readability
        for row in conn.execute(
            "SELECT name, skim_skill_md, deep_synthesis_skill_md FROM topics"
        ):
            base = _safe_filename(row["name"])
            if row["skim_skill_md"]:
                (skills_dir / f"{base}.skim_skill.md").write_text(
                    row["skim_skill_md"], encoding="utf-8"
                )
            if row["deep_synthesis_skill_md"]:
                (skills_dir / f"{base}.deep_synthesis_skill.md").write_text(
                    row["deep_synthesis_skill_md"], encoding="utf-8"
                )
    finally:
        conn.close()

    logger.info("Snapshot complete: %s", out_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
