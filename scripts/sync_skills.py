"""Sync on-disk skill Markdown files into topic rows in the DB.

The `skills/` directory is the canonical source of truth (it's in git).
This script diffs the file content against the stored `skim_skill_md` /
`deep_synthesis_skill_md` columns and, with --apply, updates the DB.

Usage
-----
List all topics with their current skill hashes:
    python scripts/sync_skills.py --list

Diff a skill file against a topic (dry-run):
    python scripts/sync_skills.py --topic "Limit Order Book" --skim skills/SKILL_limit_order_book.md
    python scripts/sync_skills.py --topic "Limit Order Book" --deep skills/SKILL_lob_deep.md

Update the DB (apply the diff):
    python scripts/sync_skills.py --topic "Limit Order Book" --skim skills/SKILL_limit_order_book.md --apply
    python scripts/sync_skills.py --topic "Limit Order Book" --skim skills/SKILL_limit_order_book.md --deep skills/SKILL_lob_deep.md --apply
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import sqlite3
import sys
from pathlib import Path

# Ensure diff output renders correctly on Windows terminals that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poneglyph.config import settings  # noqa: E402


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_list() -> None:
    conn = _get_db()
    rows = conn.execute(
        "SELECT name, skim_skill_md, deep_synthesis_skill_md FROM topics ORDER BY name"
    ).fetchall()
    conn.close()

    if not rows:
        print("No topics found in DB.")
        return

    col_w = max(len(r["name"]) for r in rows)
    header = f"{'Topic':<{col_w}}  {'Skim skill':<18}  {'Deep skill':<18}"
    print(header)
    print("-" * len(header))
    for r in rows:
        skim = _sha256(r["skim_skill_md"]) if r["skim_skill_md"] else "(none)"
        deep = _sha256(r["deep_synthesis_skill_md"]) if r["deep_synthesis_skill_md"] else "(none)"
        print(f"{r['name']:<{col_w}}  {skim:<18}  {deep:<18}")


def _diff_column(topic_name: str, column: str, db_text: str | None, file_text: str, apply: bool) -> bool:
    """Print a unified diff. Returns True if there were differences."""
    label = "skim_skill_md" if column == "skim_skill_md" else "deep_synthesis_skill_md"
    db_lines = (db_text or "").splitlines(keepends=True)
    file_lines = file_text.splitlines(keepends=True)

    diff = list(difflib.unified_diff(db_lines, file_lines, fromfile=f"DB:{label}", tofile=f"file:{label}"))
    if not diff:
        print(f"[{topic_name}] {label}: no difference")
        return False

    print(f"[{topic_name}] {label}: differs")
    for line in diff:
        print(line, end="")
    print()

    if apply:
        conn = _get_db()
        conn.execute(
            f"UPDATE topics SET {column} = ?, updated_at = datetime('now') WHERE name = ?",
            (file_text, topic_name),
        )
        conn.commit()
        conn.close()
        db_hash = _sha256(file_text)
        print(f"  → applied (new hash: {db_hash})")

    return True


def cmd_diff(topic_name: str, skim_path: Path | None, deep_path: Path | None, apply: bool) -> None:
    conn = _get_db()
    row = conn.execute(
        "SELECT name, skim_skill_md, deep_synthesis_skill_md FROM topics WHERE name = ?",
        (topic_name,),
    ).fetchone()
    conn.close()

    if row is None:
        print(f"Error: topic '{topic_name}' not found in DB.", file=sys.stderr)
        sys.exit(1)

    changed = False

    if skim_path is not None:
        if not skim_path.exists():
            print(f"Error: skim file not found: {skim_path}", file=sys.stderr)
            sys.exit(1)
        file_text = skim_path.read_text(encoding="utf-8")
        changed |= _diff_column(topic_name, "skim_skill_md", row["skim_skill_md"], file_text, apply)

    if deep_path is not None:
        if not deep_path.exists():
            print(f"Error: deep file not found: {deep_path}", file=sys.stderr)
            sys.exit(1)
        file_text = deep_path.read_text(encoding="utf-8")
        changed |= _diff_column(
            topic_name, "deep_synthesis_skill_md", row["deep_synthesis_skill_md"], file_text, apply
        )

    if not changed and not apply:
        print("Skills are in sync.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="Show all topics and their current skill hashes")

    diff_p = sub.add_parser("diff", help="Diff skill file(s) against a topic row (default when args are given)")
    diff_p.add_argument("--topic", required=True, help="Exact topic name as stored in DB")
    diff_p.add_argument("--skim", metavar="FILE", help="Path to skim skill .md file")
    diff_p.add_argument("--deep", metavar="FILE", help="Path to deep synthesis skill .md file")
    diff_p.add_argument("--apply", action="store_true", help="Write differences into the DB")

    # Also allow top-level --topic / --skim / --deep / --apply without a subcommand
    parser.add_argument("--topic", help=argparse.SUPPRESS)
    parser.add_argument("--skim", metavar="FILE", help=argparse.SUPPRESS)
    parser.add_argument("--deep", metavar="FILE", help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--list", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    # Support both subcommand and flat invocation styles
    if args.cmd == "list" or getattr(args, "list", False):
        cmd_list()
    elif args.cmd == "diff" or getattr(args, "topic", None):
        topic = args.topic
        skim = Path(args.skim) if args.skim else None
        deep = Path(args.deep) if args.deep else None
        apply = args.apply
        if not topic:
            parser.error("--topic is required")
        if not skim and not deep:
            parser.error("at least one of --skim or --deep is required")
        cmd_diff(topic, skim, deep, apply)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
