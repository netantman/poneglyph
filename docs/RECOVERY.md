# Poneglyph Recovery Runbook

How to recover from data loss or corruption. **Test this once a year** —
an untested runbook is fiction.

Backup locations (set up in Phase 4b):

| Tier | What | Where |
|---|---|---|
| 1 | Daily gzipped DB snapshots | `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups\poneglyph-YYYYMMDD-HHMM.db.gz` |
| 2 | Weekly JSONL snapshots | same dir, under `snapshot-YYYYMMDD/` |
| 3 | Pre-migration backups | `data/migration_backups/poneglyph-<reason>-<ts>.db` (last 5) |
| 4 | Off-site mirror | private GitHub repo `poneglyph-backups` (under `daily/`) |

PDFs live in `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\…`
and are versioned by OneDrive. They are **not** backed up by `backup_db.py`.

---

## Scenario 1 — Fresh machine, full restore

1. Install Python 3.11+, git.
2. `git clone <repo-url>`, `cd poneglyph`.
3. `pip install -e .` (or `pip install -r requirements.txt`).
4. Copy `.env.example` to `.env`, paste in your `ANTHROPIC_API_KEY` and the GitHub backup PAT (`BACKUP_GITHUB_TOKEN`, `BACKUP_GITHUB_REPO`).
5. Restore the latest DB snapshot:
   ```powershell
   $latest = Get-ChildItem "$env:OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups\poneglyph-*.db.gz" |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
   New-Item -ItemType Directory -Force -Path data | Out-Null
   $tmp = Join-Path $env:TEMP "poneglyph-restore.db"
   & python -c "import gzip,shutil,sys; shutil.copyfileobj(gzip.open(sys.argv[1],'rb'), open(sys.argv[2],'wb'))" $latest.FullName $tmp
   Move-Item -Force $tmp data\poneglyph.db
   ```
6. Run `python scripts/validate_db.py` — expect `all checks passed`.
7. Launch the app and spot-check a topic page.
8. Re-register the backup task: `python scripts/setup_scheduler.py`.

If OneDrive isn't reachable, pull from the GitHub mirror instead:

```powershell
git clone https://github.com/<your-username>/poneglyph-backups
# pick the most recent file in poneglyph-backups/daily/
```

Embeddings are not in JSON snapshots. They're in the binary `.db.gz` so they
restore automatically. If you restored from a JSON snapshot via
`scripts/import_snapshot.py`, regenerate them: trigger the embedding rebuild
flow inside the app (see Phase 3 docs) or rerun the relevance update from the
topic page.

---

## Scenario 2 — Recover one accidentally-deleted topic

You hit the Delete button by mistake and want one specific topic back.

1. Find a snapshot from before the delete. Snapshots are dated
   `poneglyph-YYYYMMDD-HHMM.db.gz` — pick the newest one **before** the click.
2. Decompress to a scratch path:
   ```powershell
   $snap = "<full path to .db.gz>"
   $out  = "$env:TEMP\poneglyph-restore.db"
   & python -c "import gzip,shutil,sys; shutil.copyfileobj(gzip.open(sys.argv[1],'rb'), open(sys.argv[2],'wb'))" $snap $out
   ```
3. Open both DBs in [DB Browser for SQLite](https://sqlitebrowser.org/) (free):
   - **File 1:** the scratch `.db` (read-only is fine)
   - **File 2:** the live `data/poneglyph.db`
4. Find the topic id in File 1 (`SELECT id, name FROM topics WHERE name = '...'`).
5. Re-insert into File 2, in this order:
   ```sql
   -- run against the live DB
   INSERT INTO topics SELECT * FROM main.topics WHERE id = <id>;
   INSERT INTO topic_papers SELECT * FROM main.topic_papers WHERE topic_id = <id>;
   INSERT INTO topic_paper_notes SELECT * FROM main.topic_paper_notes WHERE topic_id = <id>;
   INSERT INTO cross_syntheses SELECT * FROM main.cross_syntheses WHERE topic_id = <id>;
   INSERT INTO topic_steering_log SELECT * FROM main.topic_steering_log WHERE topic_id = <id>;
   INSERT INTO topic_embeddings SELECT * FROM main.topic_embeddings WHERE topic_id = <id>;
   ```
   (DB Browser supports cross-DB queries via "Attach Database".)
6. Run `python scripts/validate_db.py` to make sure foreign keys are clean.

Same recipe for a deleted paper — substitute `papers`, `paper_notes`,
`paper_citations`, `paper_embeddings`, plus all rows in `topic_papers` /
`topic_paper_notes` referencing that `paper_id`.

---

## Scenario 3 — DB is corrupt or won't open

1. Stop the webapp.
2. Run `sqlite3 data/poneglyph.db "PRAGMA integrity_check"`. If it errors, the
   file is unrecoverable in place.
3. Move the corrupt file aside: `mv data/poneglyph.db data/poneglyph.db.bad`.
4. Restore the most recent snapshot per Scenario 1, step 5.
5. Run `validate_db.py`. Launch the app.
6. Anything done since the last snapshot is lost. Check if any work-in-progress
   exists in `paper_notes.human_note` from your browser autosave / clipboard
   before fully closing the chapter.

---

## Scenario 4 — Bad migration

`init_db()` snapshots the DB to `data/migration_backups/` before any destructive
migration step (DROP, RENAME). Five most-recent are kept.

1. Stop the webapp.
2. List backups: `ls data/migration_backups/`.
3. Copy the newest one over the live DB:
   ```powershell
   Copy-Item -Force data\migration_backups\poneglyph-<reason>-<ts>.db data\poneglyph.db
   ```
4. Identify the bad migration step in `_migrate()` (`poneglyph/db.py`) and fix
   or revert before launching again.
5. Re-launch. `init_db()` will replay the (fixed) migration on the restored DB.

---

## Annual fire drill (do this once a year)

1. Pick yesterday's snapshot from OneDrive.
2. Restore it into a scratch directory (`%TEMP%\poneglyph-drill\`):
   - copy the repo
   - point `DATABASE_PATH` in a temp `.env` to the scratch DB
   - decompress the snapshot into that path
3. Run `python scripts/validate_db.py` against it.
4. Launch the app on a different port (`PORT=8001`). Open a topic page,
   confirm papers and synthesis content render.
5. Time how long it took. If it took more than 30 minutes, simplify this doc.
6. Note the date in your calendar and schedule next year's drill.

If any step doesn't work, **fix this doc before the next drill** — that's the
whole point of the exercise.
