# Phase 4b — Data Resiliency Plan

## 1. What we are protecting

Threat-model first. The app's irreplaceable data falls into three buckets:

| Asset | Where | Replaceable? | Cost to recreate |
|---|---|---|---|
| **SQLite DB** (`data/poneglyph.db`) | Local repo dir | No | High — contains all human notes, topics, skills, LLM output |
| **Per-topic skill prompts** (`topics.skim_skill_md`, `topics.deep_synthesis_skill_md`) | Inside DB | Partially (copies exist in `skills/`) | Medium — drift between in-DB and on-disk versions |
| **LLM-generated content** (`topic_paper_notes.*`, `cross_syntheses`, `qa_history`, `paper_notes.human_note`) | Inside DB | No (Anthropic call cost) | High — every regen costs money |
| **Embeddings** (`paper_embeddings`, `topic_embeddings`) | Inside DB | Yes (recomputable from text) | Low — local model, minutes |
| **Paper PDFs** | `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\…` | Yes (re-downloadable for arXiv; not for private uploads) | Low for arXiv, high for manual uploads |
| **`.env` (API keys)** | Repo dir, not committed | Yes (rotate keys) | Low |
| **Source code + `skills/` Markdown** | Git | Yes | None (git push) |

**Failure modes to plan for:**
1. SSD / disk failure on the dev machine.
2. Accidental `DELETE FROM topics WHERE id = ?` from a misclick — cascades wipe `topic_paper_notes`, `cross_syntheses`, `topic_embeddings`.
3. Schema migration bug — a new `_migrate()` clause mangles existing rows.
4. `git clean -fdx` or accidental `rm` of `data/`.
5. WAL corruption from a hard kill mid-write or power loss.
6. Ransomware / OneDrive sync conflict cascade.
7. Manual edits to the DB (sqlite3 CLI) that violate invariants.

PDFs are already mostly safe — they live under OneDrive, which versions and cloud-replicates them. The DB file does **not** live under OneDrive, so it's the single biggest risk.

---

## Legend for action ownership

Every step below is tagged with who has to do it:

- **[CODE]** — fully scriptable; Claude can implement it end-to-end with no human in the loop.
- **[HUMAN]** — requires a human action that no code can do for you (clicking through a UI, granting credentials, making a judgement call, running a manual drill).
- **[CODE + HUMAN]** — Claude writes the script/config; the human has to run a one-time setup step (e.g. trigger the Task Scheduler installer, paste a token into `.env`, choose between options).

---

## 2. External / out-of-codebase measures

### 2.1 Move the DB into a synced + versioned location (highest ROI)

The DB file is ~1.1 MB. Trivial to sync.

- **Recommended:** keep the DB path inside the repo for dev convenience, but configure a **scheduled local backup** (see 2.2) into OneDrive. Reason: OneDrive on an open SQLite WAL file can race the writer and produce torn copies. Don't sync the live `.db` — sync a clean snapshot.
- **[HUMAN]** Confirm the OneDrive folder you want backups under (e.g. `C:\Users\zhong\OneDrive\poneglyph_backups\`) and that OneDrive is actually syncing it. Claude can't see your OneDrive sync status.

### 2.2 Scheduled SQLite Online Backup (the key defense)

Use SQLite's built-in [Online Backup API](https://www.sqlite.org/backup.html) — safe even while the app is writing.

- **[CODE]** New script: `scripts/backup_db.py`
  - Opens source DB read-only, target as a fresh file
  - Uses `sqlite3` Python module's `conn.backup(target_conn)` (atomic, page-by-page, locks-aware)
  - Runs `PRAGMA integrity_check` on the backup before keeping it
  - Writes to `<OneDrive>\poneglyph_backups\poneglyph-YYYYMMDD-HHMM.db`
  - Retention: keep last 14 dailies, last 8 weeklies, last 6 monthlies (delete others by mtime)
  - Optional: `gzip` the file (compresses ~5×)
- **[CODE + HUMAN]** Register a **Windows Task Scheduler** entry by extending `scripts/setup_scheduler.py`:
  - Claude writes the schtasks/COM registration code. The **human** runs `python scripts/setup_scheduler.py` once with a logged-in Windows session — Task Scheduler registration requires the current user's credentials and can't be done remotely / headlessly.
  - **Daily** at a quiet hour (e.g. 03:00) — local + OneDrive copy
  - On user logon, run the script if no backup exists for "today" (laptop-friendly catch-up)
  - **[HUMAN]** Verify the task appears under Task Scheduler → "Task Scheduler Library" after install, and let one cycle run before trusting it.

### 2.3 Off-machine redundancy

OneDrive alone is one provider — fine for hardware-failure recovery, weak against account compromise / mass-delete events.

- **[HUMAN]** Pick **one** option (this is a judgement call about your existing tooling):
  - **Private GitHub repo** for backups: a separate `poneglyph-backups` repo, periodic `git commit` of the gzipped DB + a JSON dump (see 2.4). 1 MB-class files are fine in git; full history doubles as point-in-time recovery.
  - **Cloud bucket** via `rclone` (S3 / B2 / GCS / Wasabi). Cheapest tier is $0.005/GB/month — negligible. Set lifecycle to "keep 90 days versioned, then archive."
  - Recommended default: GitHub repo unless you already have an `rclone` setup.
- **[HUMAN]** If you go GitHub: create the private repo, generate a fine-grained PAT scoped only to that repo, paste it into `.env` as `BACKUP_GITHUB_TOKEN`. Claude can't create repos or generate tokens for you.
- **[HUMAN]** If you go rclone: install `rclone`, run `rclone config` interactively to authorize the cloud provider (this opens a browser — can't be scripted blindly).
- **[CODE]** Once credentials exist, Claude can write the post-backup hook in `scripts/backup_db.py` that pushes/uploads the latest snapshot.

### 2.4 JSON-dump snapshot (format-portability insurance)

Binary `.db` files are useless if SQLite ever can't open them (corruption, version skew). Hedge with a human-readable dump:

- **[CODE]** New script: `scripts/export_snapshot.py`
  - For every table, dumps `SELECT * FROM <t>` to one JSONL file per table
  - Dumps `topics.skim_skill_md` and `deep_synthesis_skill_md` as separate `.md` files keyed by topic name (also useful as a diff target — see 3.3)
  - Skips `paper_embeddings` / `topic_embeddings` (cheaply rebuildable, large)
  - Output: `<OneDrive>\poneglyph_backups\snapshot-YYYYMMDD\…`
- **[CODE]** Run weekly alongside the daily binary backup (same Task Scheduler entry).
- **[CODE]** Pair with `scripts/import_snapshot.py` to rebuild a DB from the JSONL — exercises the round-trip and proves the dump is real.

### 2.5 Treat skills in `skills/` as the source of truth

Currently the on-disk `skills/SKILL_*.md` and the per-topic `topics.skim_skill_md` / `topics.deep_synthesis_skill_md` columns can diverge silently. Establish:

- **[HUMAN]** Decide and confirm the convention: the Markdown in `skills/` is canonical (it's in git). This is a workflow choice, not a code change.
- **[CODE]** A small `scripts/sync_skills.py` that prints a diff between each file's content and the matching topic row, and (with `--apply`) updates the DB. Run before any large LLM batch so synthesized output is tagged with a `skim_skill_hash` you can later trace.

### 2.6 PDF redundancy

OneDrive already covers this; only two gaps:

- **[HUMAN]** **Manually uploaded PDFs** that aren't on arXiv have no other source. Confirm in the OneDrive web UI that the folder has **version history enabled** (it is by default for OneDrive Personal, but verify) and consider moving it into Personal Vault. Claude can't change OneDrive settings.
- **[CODE]** **Filename collisions / sync conflicts** can produce `paper (1).pdf`. Add a one-time check: `scripts/audit_pdf_paths.py` that walks `papers.pdf_local_path` and reports missing / conflicted files.
- **[HUMAN]** Run the audit script; act on what it reports (move/rename/re-link) — fixes are case-by-case and need your judgement.

### 2.7 Document the recovery runbook

Single Markdown doc — `docs/RECOVERY.md` — covering:
1. Fresh-machine restore: clone repo, copy newest `poneglyph-*.db.gz` from OneDrive, gunzip into `data/`, run app.
2. Recover one accidentally-deleted topic: open last good snapshot in a SQLite browser, `INSERT … SELECT` rows back into live DB.
3. Rebuild embeddings from scratch (since they're not in JSON snapshot).
4. How to verify: run `PRAGMA integrity_check`, count rows per table, eyeball a topic page.

- **[CODE]** Claude writes `docs/RECOVERY.md`.
- **[HUMAN]** A runbook you've never tested is a runbook that doesn't work — schedule **one annual fire drill** (restore from yesterday's backup into a scratch directory and boot the app against it). This has to be a human action — the value is in proving *you* can do it under stress.

---

## 3. Code-level robustness

The current code is correct for the happy path but has a few sharp edges around durability and accidental loss.

### 3.1 Add a `.gitignore`

**[CODE]** There isn't one. Risk: someone commits `.env` (API key leak) or `data/poneglyph.db` (binary churn + leaks human notes into git history). Add:

```
__pycache__/
*.pyc
.env
data/poneglyph.db
data/poneglyph.db-wal
data/poneglyph.db-shm
data/pdfs/tmp/
.venv/
```

The `__pycache__` files currently dirty the working tree (visible in `git status`) — adding the ignore cleans that up.

**[HUMAN]** After Claude adds `.gitignore`, run `git rm --cached -r poneglyph/__pycache__ data/poneglyph.db` (and similar) once to untrack files that are already in the index — this is a destructive git op so Claude won't do it without you.

### 3.2 Wrap multi-statement writes in transactions

**[CODE]** Today every helper in `db.py` (`execute`, `executemany`) opens a connection, runs one statement, commits, and closes. Multi-step business logic in `pipeline.py` (`_synthesize_paper` runs four `execute()` calls in sequence) is therefore **not atomic** — a crash between calls leaves a half-written `topic_paper_notes` row with stale `topic_papers.recommendation`.

Add a context-manager helper:

```python
@contextmanager
def transaction():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

Then refactor `_synthesize_paper`, `delete_paper`, `delete_topic`, and any other multi-write site to use it. This costs ~30 lines and removes a real durability hole.

### 3.3 Soft-delete for high-value rows

**[HUMAN]** Pick option 1 vs 2 below — Claude can implement either, but the call is a product/UX choice you should make.

Hard `DELETE FROM topics WHERE id = ?` cascades through `topic_paper_notes`, `cross_syntheses`, `topic_embeddings`, `topic_papers`, `topic_steering_log` — minutes of LLM money gone in one click. The UI (htmx button) doesn't have an undo.

Two options, in order of preference:
1. **[CODE]** **Soft delete** — add `deleted_at TEXT` to `topics`, `papers`, `paper_notes`, `topic_paper_notes`. Routes set `deleted_at = datetime('now')` instead of `DELETE`. Add a "Trash" view that lets the user restore or permanently purge. Filter all read queries with `WHERE deleted_at IS NULL`. Bigger change, best result.
2. **[CODE]** **Pre-delete snapshot** — before `DELETE FROM topics`, dump the affected rows into a `deleted_topics_archive` table (or a JSONL file under `data/trash/`). Smaller change, recoverable but ugly.

If 1 feels heavy, ship 2 now and revisit.

### 3.4 Backup-before-migrate

**[CODE]** `init_db()` calls `_migrate()` on every startup. `_migrate()` already drops and renames a table (`paper_notes_clean`) — that worked once, but the next migration that uses the same pattern is one bug away from data loss.

Before any `_migrate()` operation that does `DROP TABLE` / `ALTER TABLE`, copy the live DB file to `data/migration_backups/poneglyph-<schema_version>-<timestamp>.db`. Keep the last 5. Add a `schema_version` table (`PRAGMA user_version` works too) so you stop running already-applied migrations on every boot.

### 3.5 Health-check on startup

**[CODE]** In `init_db()`, after opening the connection, run:

```python
result = conn.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    logger.error("DB integrity check failed: %s", result)
    # Refuse to start, or at minimum write a loud banner to stderr
```

Catches WAL corruption before it gets compounded by writes.

### 3.6 PRAGMAs for durability

**[CODE]** `get_connection()` already sets `journal_mode=WAL` and `foreign_keys=ON`. Add:

```python
conn.execute("PRAGMA synchronous=NORMAL")  # WAL-safe, faster than FULL
conn.execute("PRAGMA wal_autocheckpoint=1000")
conn.execute("PRAGMA busy_timeout=5000")
```

`busy_timeout` matters because the app and the backup script will occasionally race; without it, the backup will get `SQLITE_BUSY` and fail silently.

### 3.7 Connection per request, not per query

**[CODE]** Minor but related: every helper (`fetch_one`, `fetch_all`, `execute`) opens and closes its own connection. That's fine for correctness but expensive when a route makes 10 calls and harmful when those 10 calls should be one transaction (see 3.2). Either:
- Add the `transaction()` context manager and use it explicitly at the boundary, or
- Move to a request-scoped connection via FastAPI dependency injection.

Pick the first — it's smaller and fits the existing style.

### 3.8 Validation script

**[CODE]** Add `scripts/validate_db.py` that runs:
- `PRAGMA integrity_check`
- `PRAGMA foreign_key_check`
- Row counts per table (with thresholds — alert if any table shrinks ≥10% week-over-week)
- For every `papers.pdf_local_path` that isn't NULL: stat the file, report missing
- For every `topic_paper_notes.deep_synthesis IS NOT NULL`: assert the matching `topic_papers` link still exists

Hook this into the same Task Scheduler job as the daily backup — only back up if validation passes (don't overwrite a good backup with a bad one).

### 3.9 Don't strip API errors

**[CODE]** Several LLM helpers return `""` on failure (`call_sonnet`, `call_sonnet_with_pdf`). When that empty string then gets persisted as `deep_synthesis = ''`, the user can't tell "we never tried" from "the API failed." Already partially fixed in `call_haiku` (returns `(text, error)`). Roll the same pattern out to the other helpers — costs nothing and means a transient API outage doesn't write empty rows that look like completed work.

---

## 4. Implementation order (suggested)

Stage so each step gives immediate protection. The "Owner" column shows who has to act:

| # | Step | Effort | Owner | Protection unlocked |
|---|---|---|---|---|
| 1 | Add `.gitignore` (+ user runs `git rm --cached` once) | 5 min | CODE + HUMAN | No more pycache churn / .env leak risk |
| 2 | `scripts/backup_db.py` (Claude) + Task Scheduler install (user runs setup script) | 1–2 h | CODE + HUMAN | Daily versioned snapshots in OneDrive |
| 3 | Health-check + extra PRAGMAs in `db.py` | 30 min | CODE | Startup catches corruption; backup races handled |
| 4 | `transaction()` helper + refactor `_synthesize_paper`, `delete_*` | 1 h | CODE | Atomic multi-writes |
| 5 | `scripts/export_snapshot.py` + weekly task | 1 h | CODE | Format-portable JSON dump |
| 6 | Soft-delete for `topics` and `papers` (user picks option 1 vs 2 first) | 2–3 h | HUMAN decides → CODE | Undo for accidental cascades |
| 7 | `scripts/validate_db.py` integrated into backup task | 1 h | CODE | Backups guaranteed clean |
| 8 | Off-machine redundancy: user creates private GitHub repo + PAT, then Claude wires push | 30 min | HUMAN setup → CODE | Survives single-cloud account loss |
| 9 | `docs/RECOVERY.md` runbook (Claude) + first restore drill (user) | 1 h | CODE + HUMAN | Proves the system works end-to-end |
| 10 | Backup-before-migrate hook in `_migrate()` | 30 min | CODE | Schema-bug insurance |

Steps 1–3 alone close the biggest gaps and cost <3 hours.

### What the human strictly has to do

If you only read one section of this doc:

1. Confirm the OneDrive backup directory you want (§2.1).
2. Run `python scripts/setup_scheduler.py` once after Claude updates it, then verify the daily task in Task Scheduler (§2.2).
3. Pick one off-site option and provision its credentials — GitHub PAT or `rclone config` (§2.3).
4. Confirm OneDrive version-history is on for the PDF folder (§2.6).
5. Decide soft-delete vs pre-delete-snapshot (§3.3).
6. After `.gitignore` lands, run `git rm --cached` on tracked-but-now-ignored files (§3.1).
7. Run the audit script and remediate any flagged PDFs (§2.6).
8. Do one annual restore drill (§2.7).

Everything else on the list Claude can do unattended.

---

## 5. Out of scope (deliberate non-goals)

- Replicated / HA SQLite (Litestream, rqlite, etc.) — overkill for a single-user research tool.
- Encrypting backups — OneDrive already encrypts at rest; add only if you switch to a public bucket.
- Continuous WAL streaming — daily snapshots are enough for this workload (low write rate, bounded blast radius).
- Migrating off SQLite — it's the right tool here; just back it up properly.
