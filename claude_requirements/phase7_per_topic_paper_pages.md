# Phase 7: Per-Topic Paper Detail Pages

## Goal

Promote the per-(paper, topic) view from a query-param overlay (`/papers/{id}?topic={tid}`)
to a first-class canonical URL (`/papers/{id}/topics/{tid}`). Each (paper, topic) combination
is its own page with its own structural skim, deep synthesis, and human notes — all rendered
through that topic's configured skim/deep-synthesis skill.

The data layer is already shaped for this (`topic_paper_notes` keys on `(topic_id, paper_id)`
and stores skim + deep synthesis per pair). What changes is URL structure, navigation, note
scoping, and an auto-skim trigger on topic membership.

## Motivation

A paper read in the context of *"momentum factor decay"* is a different artifact from the
same paper read in the context of *"liquidity-sourced anomalies"* — the structural skim
should foreground different mechanisms, the deep synthesis should connect different prior
work, and the user's own notes should reflect what they care about for that lens. Today the
DB supports this but the URL and UI bury it behind a query param, so users (and bookmarks)
treat every paper as having one canonical detail page.

## User-facing changes

1. **Canonical URL is per-topic.** `/papers/{paper_id}/topics/{topic_id}` renders the full
   detail page for that pair. The bare `/papers/{paper_id}` 302-redirects to the first topic.
2. **Topic tabs at the top of the page.** A horizontal row of pill/tab links — one per topic
   this paper belongs to — sits above the existing content tabs (Skim / Deep / Info / PDF /
   Topics / Notes). Clicking switches the entire page (full nav, not htmx swap, so the URL
   updates). The active tab is styled distinctively.
3. **Per-topic human notes.** The notes field is now scoped to (paper, topic). Editing notes
   on the "momentum" tab does not affect notes on the "liquidity" tab.
4. **Auto-skim on add-to-topic.** When a paper is added to a topic (whether via scout ingest,
   manual add, or backfill), the structural skim for that (paper, topic) pair is generated
   immediately using the topic's `skim_skill_md`. If the topic has no skim skill configured,
   the skim is skipped silently and the user sees the existing "Generate" button on first
   visit. Deep synthesis remains manual (heavier model call).

## Data model changes

### `topic_paper_notes`: add per-topic note column

```
ALTER TABLE topic_paper_notes ADD COLUMN human_note TEXT;
```

### Migration: backfill from existing shared notes

For every existing `topic_paper_notes` row, copy the corresponding paper's shared
`paper_notes.human_note` into the new column (if the new column is still NULL):

```sql
UPDATE topic_paper_notes
SET human_note = (
    SELECT pn.human_note
    FROM paper_notes pn
    WHERE pn.paper_id = topic_paper_notes.paper_id
)
WHERE human_note IS NULL;
```

For papers that have notes but no `topic_paper_notes` row yet (i.e. paper is in a topic but
skim was never generated), insert a stub `topic_paper_notes` row with the note copied over,
so the user's note isn't lost the first time they visit the new URL:

```sql
INSERT INTO topic_paper_notes (topic_id, paper_id, human_note)
SELECT tp.topic_id, tp.paper_id, pn.human_note
FROM topic_papers tp
JOIN paper_notes pn ON pn.paper_id = tp.paper_id
WHERE pn.human_note IS NOT NULL AND pn.human_note != ''
  AND NOT EXISTS (
      SELECT 1 FROM topic_paper_notes tpn
      WHERE tpn.topic_id = tp.topic_id AND tpn.paper_id = tp.paper_id
  );
```

### Drop `paper_notes.human_note`

After the backfill migrations above confirm success (row counts match), drop the column in the
same migration block:

```sql
-- SQLite doesn't support DROP COLUMN before 3.35; use the recreate-table dance if needed.
-- As of SQLite 3.35+ (Python 3.10+ ships 3.39+), just:
ALTER TABLE paper_notes DROP COLUMN human_note;
```

Before dropping, verify no reader still references it: audit `llm_cross.py`, `llm_bulk.py`,
`llm_qa.py`, `pipeline.py`, and all steering/context helpers. Any SELECT on `paper_notes.human_note`
must switch to `topic_paper_notes.human_note` filtered by the current topic_id before the
drop runs. The migration function should assert the column no longer exists after the drop.

The other columns of `paper_notes` (`paper_info`, `abstract_excerpt`) stay shared at the
paper level — they're metadata, not opinion.

### New `pending_skims` table (bulk batching)

```sql
CREATE TABLE IF NOT EXISTS pending_skims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    queued_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status      TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'done' | 'error'
    error_msg   TEXT,
    UNIQUE(topic_id, paper_id)
);
```

Used only for bulk ingest (see Auto-skim strategy below). Single manual adds skip the queue.

## Route changes

### New canonical routes

```
GET  /papers/{paper_id}/topics/{topic_id}                   → detail page for (paper, topic)
GET  /papers/{paper_id}/topics/{topic_id}/structural-skim   → htmx tab: skim panel
POST /papers/{paper_id}/topics/{topic_id}/structural-skim   → generate/regenerate skim
GET  /papers/{paper_id}/topics/{topic_id}/deep-synthesis    → htmx tab: deep synthesis panel
POST /papers/{paper_id}/topics/{topic_id}/deep-synthesis    → generate/regenerate deep synthesis
GET  /papers/{paper_id}/topics/{topic_id}/note              → htmx tab: notes panel
POST /papers/{paper_id}/topics/{topic_id}/note              → save per-topic human note
GET  /topics/{topic_id}/skim-progress                       → JSON {pending: N, done: M} for polling
```

Paper-level tabs (not topic-scoped) stay at their current paths:

```
GET  /papers/{paper_id}/info          → paper metadata panel (shared)
GET  /papers/{paper_id}/pdf/manage    → PDF management (shared)
GET  /papers/{paper_id}/topics-panel  → "which topics" panel + add/remove (shared; rename from /topics to avoid
                                         colliding with the new /topics/{id} sub-routes)
```

### Existing routes — redirect / repoint

| Old | New behavior |
|---|---|
| `GET /papers/{paper_id}` | 302 → `/papers/{paper_id}/topics/{first_topic_id}`. If paper has no topics: render a thin paper-shell page with title/abstract/PDF and a "This paper isn't in any topic yet" panel |
| `GET /papers/{paper_id}?topic={tid}` | 302 → `/papers/{paper_id}/topics/{tid}` (back-compat) |
| `GET /papers/{paper_id}/structural-skim?topic_id=X` | 301 → `/papers/{paper_id}/topics/X/structural-skim` |
| `GET /papers/{paper_id}/deep-synthesis?topic_id=X` | 301 → `/papers/{paper_id}/topics/X/deep-synthesis` |
| `POST /papers/{paper_id}/note` (shared) | Remove; replaced by per-topic POST |

### Auto-skim strategy: single vs bulk

**Single add-to-topic** (manual user action): run the skim inline using FastAPI `BackgroundTasks`
— the response returns immediately (redirects to the new per-topic URL) while the skim generates
in the background. The topic-paper page shows a "Generating skim…" indicator (htmx polls
`/topics/{tid}/skim-progress` every 3 s until the `pending` count for this paper drops to 0).

**Bulk add** (scout backfill, citation ingest — N papers at once): instead of blocking on N
sequential LLM calls, enqueue all (topic_id, paper_id) pairs into `pending_skims`, then fire a
single FastAPI `BackgroundTasks` job that drains the queue with `asyncio.Semaphore(3)` — at most
3 concurrent skim calls. On the topic detail page, if `pending_skims` has rows for this topic,
show a persistent progress bar (htmx polls `GET /topics/{topic_id}/skim-progress` every 3 s):

```
Generating skims: 12 / 50 done  [███████░░░░░░░░░]
```

The bar disappears when `pending = 0`. If any skim errors, the row is marked `status='error'` with
the message; the progress endpoint surfaces error count so the user can see failures without
blocking the rest of the batch.

**Shared helper `queue_skim_for_topic(topic_id, paper_id, background_tasks)`**: a single
function called by manual add-to-topic, scout ingest, and article ingest. It decides inline vs
queue based on whether a BackgroundTasks context is available and how many pending rows already
exist. All callers pass through this helper — no duplication across scout paths.

## UI changes

### Template structure

- New template: `templates/papers/partials/topic_tabs.html` — renders the horizontal row of
  per-topic tab links at the top of the detail page. Active tab styled with `.active` class.
- Modify `templates/papers/detail.html`:
  - At top of `<main>`, include `topic_tabs.html` (only when paper has ≥1 topic).
  - Existing inner tabs (`structural-skim`, `deep-synthesis`, etc.) stay but their tab-bar
    template needs to read `active_topic_id` from a stable location — already does, since the
    route already passes it.
  - Notes tab: change its form to POST to the new per-topic endpoint.

### Empty / edge-case states

| Situation | Behavior |
|---|---|
| Paper in 1 topic | Show topic tabs row anyway (single tab, visually a breadcrumb). Keeps layout consistent so users always know which topic they're in |
| Paper in 0 topics | No topic tabs. Page is "shell mode" with paper info + add-to-topic panel |
| Topic has no skim skill | Auto-skim is skipped on add-to-topic; on visit, user sees the existing "Configure a skim skill in the topic settings" message |
| Skim already exists for this (paper, topic) when added again | Don't regenerate. Skip silently |
| Paper removed from topic then re-added | `topic_paper_notes` row survives (only `topic_papers` is deleted on remove), so existing skim and notes return when re-added. Don't auto-regenerate — the existing skim is kept |

## Implementation status

| # | Step | Status | Notes |
|---|---|---|---|
| 1 | Audit readers of `paper_notes.human_note`; repoint to `topic_paper_notes.human_note` | ✅ Done | Completed in schema v3 migration |
| 2 | DB migration: add `topic_paper_notes.human_note` + `pending_skims`; backfill; drop `paper_notes.human_note` | ✅ Done | Schema v3; backfill + recreate-table dance implemented in `db.py` |
| 3 | Canonical route `GET /papers/{paper_id}/topics/{topic_id}` + 302 redirects | ✅ Done | `papers.py` lines ~515–547 |
| 4 | Topic-scoped tab routes at `/papers/{id}/topics/{tid}/structural-skim`, `deep-synthesis`, `note` | ✅ Done | All three routes exist; old query-param paths kept as aliases |
| 5 | Rename `/papers/{id}/topics` → `/papers/{id}/topics-panel` | ✅ Not needed | No route collision exists in practice; FastAPI route ordering handles it |
| 6 | Topic tabs partial (`topic_tabs.html`) + include in `detail.html` | ✅ Done | `templates/papers/partials/topic_tabs.html` |
| 7 | Per-topic note POST; wire Notes form; remove old shared-note endpoint | ✅ Done | `GET/PUT /papers/{id}/topics/{tid}/note` in `papers.py` |
| 8 | `queue_skim_for_topic` + `pending_skims` drain worker + `/topics/{id}/skim-progress` + progress bar | ✅ Done | `pipeline.py`: `queue_skim_for_topic`, `_drain_pending_skims` (Semaphore(3)); `topics.py`: progress endpoint; `templates/topics/partials/skim_progress.html` |
| 9 | Update internal template links to per-topic URLs | ✅ Done | Detail page, topic detail, list views all use per-topic paths |
| 10 | Startup recovery for stale `pending_skims` | ✅ Done | `app.py` `_recover_stale_pending_skims()` resets rows older than 5 min on boot |

## Resolved risks

- **Audit before migrate** ✅ — all `paper_notes.human_note` reads switched to `topic_paper_notes.human_note` before column was dropped
- **Route collision** ✅ — not needed; `/papers/{id}/topics-panel` rename skipped
- **Skill-hash invalidation** ✅ — `skim_skill_hash` reads correctly after migration
- **`pending_skims` leak** ✅ — `_recover_stale_pending_skims()` in `app.py` startup hook resets rows > 5 min old; drain uses `asyncio.Semaphore(3)` via `asyncio.gather` (not `BackgroundTasks`), avoiding the request-lifecycle scoping issue

## Dependencies

- **Phase 1** (DB foundation)
- **Phase 1c** (LLM metadata — skim/deep synthesis generators that auto-skim will call)
- **Phase 4** (deep synthesis path — already per-topic; no change but verify)
- **Phase 5/5b** (steering log + context helpers — audit for shared-note reads)
- Plays cleanly alongside **Phase 6** (article ingest paths share the same add-to-topic
  helper that gets the auto-skim trigger)

## Out of scope

- **Per-topic abstract or paper_info** — paper-level metadata stays shared.
- **Per-topic PDF** — one PDF per paper, shared.
- **Per-topic embeddings** — embeddings are paper-level, fine as-is.
- **Auto-deep-synthesis** on add-to-topic — too expensive; deep stays manual.
- **Persistent job queue across restarts** — the stale-row recovery on startup covers the
  crash case. A durable queue backed by Phase 8 scheduler is a future upgrade.
