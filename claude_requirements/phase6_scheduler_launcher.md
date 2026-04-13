# Phase 6: Scheduler & Launcher

## Deliverables

### Scheduler
- `scheduler_entry.py` CLI script with `--mode scout` (weekly) and `--mode cross-synthesis` (monthly)
- `setup_scheduler.py` to register both weekly and monthly Windows Task Scheduler tasks
- Scout run logging viewable in webapp

### Desktop Launcher
- `scripts/launch_webapp.pyw` — double-click to start server + open browser (no console window)
- `setup_scheduler.py` also creates a desktop shortcut (`.lnk`) pointing to `launch_webapp.pyw`

## Details

**Scouting and synthesis are independent of the webapp.** `scheduler_entry.py` is a standalone CLI that imports services directly, writes to the DB, and exits. No dependency on FastAPI or uvicorn. Both processes share the same SQLite database and service layer.

**Scheduler**: Windows Task Scheduler calls `scheduler_entry.py` on two schedules:
- Weekly: `--mode scout` — for each active topic, runs citation-graph scouting using only
  **seed papers** (`topic_papers.is_scout_seed = 1`). Topics with no seeds are skipped with
  a logged warning. Newly discovered papers are added to the topic but are NOT seeds by default.
- Monthly: `--mode cross-synthesis` — runs cross-paper synthesis for all topics

**Launcher**: `launch_webapp.pyw` is a `.pyw` file (runs via `pythonw.exe`, no visible console window):
1. Starts uvicorn as a subprocess
2. Opens `http://127.0.0.1:8000` in the default browser
3. Stays alive to keep the server running; closing it stops the server

The desktop shortcut makes it a double-click to go from nothing to the webapp in a browser.

**Future**: Could add a system tray icon via `pystray` with "Open in browser" and "Stop server" options.

## Dependencies
- All prior phases (this wires everything together for unattended operation)
