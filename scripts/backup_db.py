"""Daily SQLite Online Backup for Poneglyph.

Workflow:
  1. Open the live DB read-only and call sqlite3 .backup() into a fresh file.
     This is atomic, page-by-page, and safe even while the webapp is writing.
  2. Run PRAGMA integrity_check on the snapshot. Discard if it isn't 'ok'.
  3. Gzip the snapshot to OneDrive: <PONEGLYPH_BACKUP_DIR>/poneglyph-YYYYMMDD-HHMM.db.gz
  4. Prune old backups (14 daily / 8 weekly / 6 monthly).
  5. If BACKUP_GITHUB_TOKEN + BACKUP_GITHUB_REPO are set in .env, push the new
     snapshot to the private GitHub backup repo. Failure is logged but doesn't
     fail the whole run — OneDrive copy is the primary line of defense.

Run on demand:        python scripts/backup_db.py
Scheduled (Windows):  registered by scripts/setup_scheduler.py
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the project root is importable when run as a standalone script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poneglyph.config import settings  # noqa: E402

# Load .env so BACKUP_GITHUB_TOKEN etc. are available even when the script is
# launched by Task Scheduler (which doesn't read repo .env files automatically).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backup_db")

DEFAULT_BACKUP_DIR = Path(
    r"C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups"
)
KEEP_DAILY = 14
KEEP_WEEKLY = 8
KEEP_MONTHLY = 6


def _backup_dir() -> Path:
    override = os.environ.get("PONEGLYPH_BACKUP_DIR")
    return Path(override) if override else DEFAULT_BACKUP_DIR


def _online_backup(src: Path, dst: Path) -> None:
    """Copy src → dst using SQLite's Online Backup API. Safe with concurrent writers."""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok"
    finally:
        conn.close()


def _gzip(src: Path, dst: Path) -> None:
    with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)


def _classify(name: str) -> str:
    """Bucket a backup filename: daily | weekly | monthly. Format: poneglyph-YYYYMMDD-HHMM.db.gz."""
    try:
        stem = name.split("poneglyph-", 1)[1]
        ts = datetime.strptime(stem[:13], "%Y%m%d-%H%M")
    except (IndexError, ValueError):
        return "daily"
    if ts.day == 1:
        return "monthly"
    if ts.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


def _prune(backup_dir: Path) -> None:
    """Keep the N most-recent files in each bucket. Older files are deleted."""
    files = sorted(
        backup_dir.glob("poneglyph-*.db.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    keep_per_bucket = {"daily": KEEP_DAILY, "weekly": KEEP_WEEKLY, "monthly": KEEP_MONTHLY}
    seen: dict[str, int] = {"daily": 0, "weekly": 0, "monthly": 0}
    for f in files:
        bucket = _classify(f.name)
        seen[bucket] += 1
        if seen[bucket] > keep_per_bucket[bucket]:
            try:
                f.unlink()
                logger.info("Pruned old backup: %s", f.name)
            except OSError as exc:
                logger.warning("Could not prune %s: %s", f, exc)


def _push_to_github(snapshot_path: Path) -> None:
    """If BACKUP_GITHUB_TOKEN + BACKUP_GITHUB_REPO are set, commit/push the snapshot.

    Repo layout inside the backup repo:
        daily/poneglyph-YYYYMMDD-HHMM.db.gz

    Failure is logged and swallowed — never breaks the local backup.
    """
    token = os.environ.get("BACKUP_GITHUB_TOKEN", "").strip()
    repo_url = os.environ.get("BACKUP_GITHUB_REPO", "").strip()
    if not token or not repo_url:
        logger.info("GitHub off-site push skipped: BACKUP_GITHUB_TOKEN/BACKUP_GITHUB_REPO not set")
        return
    if not repo_url.startswith("https://"):
        logger.warning("BACKUP_GITHUB_REPO must be an https URL; got %s", repo_url)
        return

    # Inject the token into the URL: https://x-access-token:<token>@github.com/...
    auth_url = repo_url.replace("https://", f"https://x-access-token:{token}@", 1)

    work = Path(tempfile.mkdtemp(prefix="poneglyph-backup-"))
    try:
        # Shallow clone — we only need the index, not history
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, str(work)],
            check=True, capture_output=True, text=True,
        )
        daily_dir = work / "daily"
        daily_dir.mkdir(exist_ok=True)
        dest = daily_dir / snapshot_path.name
        shutil.copy2(snapshot_path, dest)

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "poneglyph-backup",
            "GIT_AUTHOR_EMAIL": "poneglyph-backup@local",
            "GIT_COMMITTER_NAME": "poneglyph-backup",
            "GIT_COMMITTER_EMAIL": "poneglyph-backup@local",
        }
        subprocess.run(["git", "-C", str(work), "add", "daily/"], check=True, env=env)
        # If nothing changed (re-run on same minute), commit will fail — fine.
        commit = subprocess.run(
            ["git", "-C", str(work), "commit", "-m", f"backup {datetime.now():%Y-%m-%d %H:%M}"],
            env=env, capture_output=True, text=True,
        )
        if commit.returncode != 0:
            if "nothing to commit" in commit.stdout + commit.stderr:
                logger.info("GitHub push: nothing to commit (already pushed)")
                return
            logger.warning("git commit failed: %s", commit.stderr.strip())
            return
        push = subprocess.run(
            ["git", "-C", str(work), "push", "origin", "HEAD"],
            env=env, capture_output=True, text=True,
        )
        if push.returncode != 0:
            logger.warning("git push failed: %s", push.stderr.strip())
            return
        logger.info("GitHub push ok: %s", snapshot_path.name)
    except subprocess.CalledProcessError as exc:
        logger.warning("GitHub push failed (subprocess): %s", exc.stderr or exc)
    except Exception as exc:
        logger.warning("GitHub push failed: %s", exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _have_recent_backup(backup_dir: Path, within_hours: int = 12) -> bool:
    """True if any *.db.gz under backup_dir is younger than `within_hours`."""
    cutoff = datetime.now() - timedelta(hours=within_hours)
    for f in backup_dir.glob("poneglyph-*.db.gz"):
        if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
            return True
    return False


def main() -> int:
    src = Path(settings.database_path)
    if not src.exists():
        logger.error("Live DB not found at %s — nothing to back up", src)
        return 1

    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    # On-logon catch-up: if we already backed up in the last 12 hours, skip.
    # The daily 03:00 trigger will re-run when due.
    if "--skip-if-recent" in sys.argv and _have_recent_backup(backup_dir):
        logger.info("Recent backup exists — skipping (--skip-if-recent)")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    final_name = f"poneglyph-{ts}.db.gz"
    final_path = backup_dir / final_name

    with tempfile.TemporaryDirectory(prefix="poneglyph-backup-") as tmp:
        staging_db = Path(tmp) / "snapshot.db"
        _online_backup(src, staging_db)
        if not _integrity_ok(staging_db):
            logger.error("integrity_check failed on snapshot — refusing to publish")
            return 2
        _gzip(staging_db, final_path)
    logger.info("Backup written: %s (%.1f KB)", final_path, final_path.stat().st_size / 1024)

    _prune(backup_dir)
    _push_to_github(final_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
