# Phase 8: Scheduler & Launcher

## Deliverables

### Scheduler
- `scheduler_entry.py` CLI script with three modes:
  - `--mode scout` (weekly) — citation-graph scouting from seed papers
  - `--mode article-scout` (daily) — RSS poll for subscribed authors per topic (CLI mode built in Phase 6; this phase registers the trigger)
  - `--mode cross-synthesis` (monthly) — unified-but-tiered cross-paper synthesis
- `setup_scheduler.py` to register all three Windows Task Scheduler triggers (daily / weekly / monthly)
- Scout run logging viewable in webapp (covers all three modes)

### Desktop Launcher
- `scripts/launch_webapp.pyw` — double-click to start server + open browser (no console window)
- `setup_scheduler.py` also creates a desktop shortcut (`.lnk`) pointing to `launch_webapp.pyw`

## Details

**Scouting and synthesis are independent of the webapp.** `scheduler_entry.py` is a standalone CLI that imports services directly, writes to the DB, and exits. No dependency on FastAPI or uvicorn. Both processes share the same SQLite database and service layer.

**Scheduler**: Windows Task Scheduler calls `scheduler_entry.py` on three schedules:
- Daily: `--mode article-scout` — for each `(topic, author)` opt-in pair (`topic_authors.active = 1`),
  fetch the author's RSS feed (with conditional GET), apply the relevance gate against the topic's
  keywords / problem statements / recent human notes, ingest matches as `papers` rows with
  `content_type='article'`, run the article-skill synthesis on public posts, skip paywalled posts.
  See Phase 6 for the full ingest pipeline.
- Weekly: `--mode scout` — for each active topic, runs citation-graph scouting using only
  **seed papers** (`topic_papers.is_scout_seed = 1`). Topics with no seeds are skipped with
  a logged warning. Newly discovered papers are added to the topic but are NOT seeds by default.
- Monthly: `--mode cross-synthesis` — runs cross-paper synthesis for all topics (unified-but-tiered:
  papers and articles are synthesized together with explicit evidence-tier tagging — see Phase 6).

**Launcher**: `launch_webapp.pyw` is a `.pyw` file (runs via `pythonw.exe`, no visible console window):
1. Starts uvicorn as a subprocess
2. Opens `http://127.0.0.1:8000` in the default browser
3. Stays alive to keep the server running; closing it stops the server

The desktop shortcut makes it a double-click to go from nothing to the webapp in a browser.

**Future**: Could add a system tray icon via `pystray` with "Open in browser" and "Stop server" options.

## Dependencies
- All prior phases (this wires everything together for unattended operation)
