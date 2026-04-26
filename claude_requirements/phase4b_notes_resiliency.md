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
- **Backup location (decided):** `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups\` — folder already exists. Claude will hard-code this as the default (overridable via `PONEGLYPH_BACKUP_DIR` env var) in `scripts/backup_db.py`.

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
  - Claude writes the schtasks/COM registration code.
  - **When the human runs it:** Claude will explicitly prompt with "Now run `python scripts/setup_scheduler.py`" once the script changes are in. Task Scheduler registration requires the current user's logged-in Windows session and can't be done remotely / headlessly.
  - The script will register two triggers: **daily** at 03:00 and **on user logon** (laptop-friendly catch-up if the machine was off at 03:00).
  - **[HUMAN]** Verify the task appears under Task Scheduler → "Task Scheduler Library" → look for `Poneglyph Daily Backup`, and let one cycle run before trusting it.

### 2.3 Off-machine redundancy — Private GitHub repo (decided)

OneDrive alone is one provider — fine for hardware-failure recovery, weak against account compromise / mass-delete events. Off-machine choice locked in: **private GitHub repo** containing daily gzipped DB snapshots and weekly JSON dumps.

#### 2.3.1 [HUMAN] Create the private backup repo

1. Go to https://github.com/new while logged into your GitHub account.
2. **Repository name:** `poneglyph-backups`
3. **Owner:** your personal account.
4. **Visibility:** **Private** (this is non-negotiable — the DB contains your unredacted human notes and full LLM output).
5. **Do not** initialise with a README, .gitignore, or license — leave it empty so the first push from the backup script is clean.
6. Click **Create repository**.
7. Note the URL (e.g. `https://github.com/<your-username>/poneglyph-backups`) — you'll paste it into `.env` in step 2.3.3.

#### 2.3.2 [HUMAN] Generate a fine-grained Personal Access Token (PAT)

Fine-grained PATs let you scope permissions to a single repo, which is what you want — even if the token leaks, the blast radius is one private backup repo, not your whole GitHub account.

1. Go to https://github.com/settings/personal-access-tokens (Settings → Developer settings → Personal access tokens → **Fine-grained tokens**).
2. Click **Generate new token**.
3. Fill in the form:
   - **Token name:** `poneglyph-backups (write)` — descriptive so you recognise it later.
   - **Resource owner:** your personal account (the one that owns the new repo).
   - **Expiration:** pick the longest GitHub allows for your account (typically 1 year). Set a calendar reminder for 11 months out — when it expires, the backup script will start failing silently otherwise.
   - **Description:** "Used by `scripts/backup_db.py` to push DB snapshots to `poneglyph-backups`."
4. **Repository access:** select **Only select repositories**, then in the dropdown pick only `poneglyph-backups`. Do **not** grant access to all repos.
5. **Permissions** — under the **Repository permissions** section (leave Account permissions empty):
   - **Contents:** **Read and write** (lets the script push commits)
   - **Metadata:** **Read-only** (auto-selected as a dependency — leave it)
   - Leave every other permission as **No access**.
6. Click **Generate token**.
7. **Copy the token immediately.** GitHub shows it exactly once. It looks like `github_pat_11ABC…`.
8. If you lose it before pasting it into `.env`, just regenerate — old token can be revoked from the same settings page.

#### 2.3.3 [HUMAN] Add the token to `.env`

Open `C:\Users\zhong\source\repos\poneglyph\.env` and append:

```
# GitHub fine-grained PAT for poneglyph-backups repo (write access, expires YYYY-MM-DD)
BACKUP_GITHUB_TOKEN=github_pat_11ABC…paste_here…
BACKUP_GITHUB_REPO=https://github.com/<your-username>/poneglyph-backups.git
```

Replace `<your-username>` and the token value. Save the file. **Do not** commit `.env` — it's already in the planned `.gitignore` from §3.1.

> Sanity check: run `git check-ignore -v .env` after `.gitignore` lands. If it prints a `.gitignore:N:.env  .env` line, you're safe.

#### 2.3.4 [CODE] Wire the push into the backup script

Once `BACKUP_GITHUB_TOKEN` and `BACKUP_GITHUB_REPO` are present in `.env`, Claude implements:

- A `_push_to_github(snapshot_path)` helper in `scripts/backup_db.py` that:
  - Clones the backup repo into a temp dir on first run, pulls on subsequent runs.
  - Copies the new gzipped snapshot into the working tree (path: `daily/poneglyph-YYYYMMDD-HHMM.db.gz`, plus `weekly/snapshot-YYYYMMDD/…` for the JSON dump on the weekly cadence).
  - Commits with message `backup YYYY-MM-DD HH:MM` and pushes using the token via the URL form `https://x-access-token:${BACKUP_GITHUB_TOKEN}@github.com/<user>/poneglyph-backups.git`.
  - On failure (no network, expired token, etc.) logs to stderr but does **not** fail the local backup — OneDrive copy is still the primary line of defense.
- Retention on the GitHub side: keep all daily snapshots for 90 days, then prune via `git rebase` / `git filter-repo` only if repo size becomes a concern. 1 MB × 365 days ≈ 365 MB — safely within GitHub's free-tier soft limit.

#### 2.3.5 [HUMAN] Verify the first push

After Claude finishes and you've run a manual `python scripts/backup_db.py`:

1. Refresh https://github.com/<your-username>/poneglyph-backups in the browser.
2. Confirm a `daily/` directory appears with one `.db.gz` file in it.
3. Confirm the commit author/email looks reasonable (the script will set `git -c user.email=poneglyph-backup@local`).

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

OneDrive already covers PDFs. One residual gap worth handling:

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

### 3.3 Beef up the delete confirmation dialog

**[CODE]** Today the UI already shows a confirm dialog before delete (`hx-confirm` on the htmx button), but the message is generic and doesn't tell the user what they're about to lose:

- [topics/detail.html:86](templates/topics/detail.html:86) and [topics/partials/topic_row.html:31](templates/topics/partials/topic_row.html:31) — `"Delete topic '{name}'? This cannot be undone."`
- [papers/detail.html:246](templates/papers/detail.html:246) — `"Delete paper '{title}'? This cannot be undone."`

That's most of the protection already. The remaining gap is informational — the user doesn't know that deleting a topic also nukes every Haiku skim and Sonnet synthesis under it, or that deleting a paper kills their human notes for every topic it belongs to.

Fix: rewrite the two `hx-confirm` strings to be specific about the cascade, and to point at the backup as the recovery path:

- **Topic:** `"Delete topic '{name}'?\n\nThis also permanently deletes:\n• Structural skims and deep syntheses for every paper in this topic\n• Cross-paper synthesis history\n• Embeddings and steering log\n• Q&A history scoped to this topic\n\nThe paper records themselves are kept. Recovery requires restoring last night's backup. Continue?"`
- **Paper:** `"Delete paper '{title}'?\n\nThis also permanently deletes:\n• Your human notes on this paper\n• Structural skims and deep syntheses across every topic it belongs to\n• Citation links\n• Embeddings\n\nRecovery requires restoring last night's backup. Continue?"`

Browser-native `confirm()` ignores `\n` in some browsers — if the formatting matters, replace `hx-confirm` with a tiny JS handler that pops a real `<dialog>` element. Not strictly necessary; one long sentence is fine if you'd rather keep the change minimal.

Combined with the daily backup from §2.2, this is enough protection for a single-user app: the dialog stops accidental clicks, and the worst-case recovery is "restore last night's snapshot." Anything heavier (soft-delete, trash view) is over-engineered for the threat model and was dropped from the plan.

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
| 6 | Rewrite delete confirm dialogs to spell out cascade + point at backup | 15 min | CODE | Misclick prevention |
| 7 | `scripts/validate_db.py` integrated into backup task | 1 h | CODE | Backups guaranteed clean |
| 8 | Off-machine redundancy: user creates private GitHub repo + PAT, then Claude wires push | 30 min | HUMAN setup → CODE | Survives single-cloud account loss |
| 9 | `docs/RECOVERY.md` runbook (Claude) + first restore drill (user) | 1 h | CODE + HUMAN | Proves the system works end-to-end |
| 10 | Backup-before-migrate hook in `_migrate()` | 30 min | CODE | Schema-bug insurance |

Steps 1–3 alone close the biggest gaps and cost <3 hours.

### What the human strictly has to do

If you only read one section of this doc:

1. ~~Confirm the OneDrive backup directory~~ — **decided**: `C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides\poneglyph_backups\` (folder created).
2. After Claude finishes the backup script, run `python scripts/setup_scheduler.py` once when prompted, then verify `Poneglyph Daily Backup` appears in Task Scheduler (§2.2).
3. Create the `poneglyph-backups` private GitHub repo, generate a fine-grained PAT, paste `BACKUP_GITHUB_TOKEN` and `BACKUP_GITHUB_REPO` into `.env` — full step-by-step in §2.3.1–2.3.3.
4. After `.gitignore` lands, run `git rm --cached` on tracked-but-now-ignored files (§3.1).
5. Run the audit script and remediate any flagged PDFs (§2.6).
6. Verify the first GitHub push succeeded (§2.3.5).
7. Do one annual restore drill (§2.7).

Everything else on the list Claude can do unattended.

---

## 5. Out of scope (deliberate non-goals)

- Replicated / HA SQLite (Litestream, rqlite, etc.) — overkill for a single-user research tool.
- Encrypting backups — OneDrive already encrypts at rest; add only if you switch to a public bucket.
- Continuous WAL streaming — daily snapshots are enough for this workload (low write rate, bounded blast radius).
- Migrating off SQLite — it's the right tool here; just back it up properly.
