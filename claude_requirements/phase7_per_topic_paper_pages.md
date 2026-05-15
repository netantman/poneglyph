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

### Deprecate (but do not drop) `paper_notes.human_note`

Leave the column in place as a read-only backup of pre-migration state. A later cleanup phase
can drop it once we're confident nothing reads it. The migration is logged in the steering
log so the trail is auditable.

The other columns of `paper_notes` (`paper_info`, `abstract_excerpt`) stay shared at the
paper level — they're metadata, not opinion.

## Route changes

### New canonical route

```
GET  /papers/{paper_id}/topics/{topic_id}            → detail page for (paper, topic)
POST /papers/{paper_id}/topics/{topic_id}/note       → save per-topic human note
```

### Existing routes — redirect / repoint

| Old | New behavior |
|---|---|
| `GET /papers/{paper_id}` | 302 → `/papers/{paper_id}/topics/{first_topic_id}`. If paper has no topics: render a thin paper-shell page with title/abstract/PDF and a "This paper isn't in any topic yet — add to one to view skim/synthesis" panel listing all topics with an "Add" button each |
| `GET /papers/{paper_id}?topic={tid}` | 302 → `/papers/{paper_id}/topics/{tid}` (back-compat for bookmarks) |
| `GET /papers/{paper_id}/structural-skim?topic_id=X` | Keep as-is; called by htmx tab swaps inside the page |
| `GET /papers/{paper_id}/deep-synthesis?topic_id=X` | Keep as-is |
| `GET /papers/{paper_id}/info` | Keep as-is (paper-level info is shared) |
| `GET /papers/{paper_id}/pdf/manage` | Keep as-is (PDF is paper-level) |
| `POST /papers/{paper_id}/note` (current shared-note endpoint) | Remove. The form now POSTs to the per-topic note endpoint |

The inner htmx tabs (skim / deep / info / PDF / topics / notes) keep their existing endpoints
and continue to accept `topic_id` as a query/form param, since they're inner swaps within the
outer page that's already scoped to a topic. Rewriting them under
`/papers/{paper_id}/topics/{topic_id}/...` is *not* required for v1 — call out as a follow-up.

### Add-to-topic — trigger auto-skim

The existing `POST /papers/{paper_id}/add-to-topic` endpoint (which inserts a `topic_papers`
row) needs an extension: after the insert, if the topic has a non-empty `skim_skill_md` and
no `topic_paper_notes` row exists yet for this (paper, topic), call the same skim generator
that the "Generate" button uses. Run inline (the existing generator is already a single LLM
call with abstract input — fast enough not to need a background queue for v1). Wrap in a
try/except that logs failures to `topic_steering_log` and proceeds, so a single failed skim
doesn't block the topic-add response.

Scout ingest paths (`citation_scout.py`, future `article_relevance.py` from Phase 6) already
call into a common `_upsert_paper` / link-to-topic helper — extend that helper, not each
caller, so all ingest paths share the auto-skim trigger.

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

## Implementation order

| # | Step | Notes |
|---|---|---|
| 1 | DB migration: add `topic_paper_notes.human_note` column + backfill from `paper_notes.human_note` (both UPDATE and INSERT-stub flows) | Wrap in the same backup-before-migrate hook from phase 4b §3.4 |
| 2 | New canonical route `GET /papers/{paper_id}/topics/{topic_id}`; rendering = existing detail flow but parameterized cleanly | At this point the page works at both URLs |
| 3 | Old-URL redirects: `/papers/{id}` → first topic; `/papers/{id}?topic=X` → new URL | Verify bookmarks/external links still work |
| 4 | Topic tabs partial + include in `detail.html` | Visual surface |
| 5 | Per-topic note endpoint + repoint the Notes form. Remove old shared-note endpoint | Notes now scoped |
| 6 | Auto-skim on add-to-topic (shared helper used by manual add, citation scout, and article scout) | Final UX promise delivered |
| 7 | Update all internal links across templates (`topics/detail.html`, `papers/list.html`, search results, scout-run views) to use the new URL when a topic context is known | Anywhere we currently link to `/papers/{id}` from inside a topic context |
| 8 | Smoke test: papers with 0, 1, and multiple topics; add/remove from topic with and without skill configured; verify backfill notes intact | |

## Risks and watchouts

- **`paper_notes` is keyed `paper_id UNIQUE`** — the existing shared-note code relies on
  one-note-per-paper. Removing that endpoint cleanly is fine, but search the codebase for any
  other reader of `paper_notes.human_note` (scout context bundles, cross-synthesis context,
  the steering-suggestion helper from phase 5/5b) — those need to switch to reading from
  `topic_paper_notes.human_note` filtered by the current topic. **Do not skip this audit** —
  silently reading the wrong source will make synthesis context drift.
- **Cross-synthesis input** (`llm_cross.py`) already runs per topic, so swapping its note
  source to per-topic notes is the natural change. Verify this is wired before declaring
  done.
- **Auto-skim on add-to-topic** runs inline. If a user mass-adds 50 papers to a topic via a
  scout backfill, that's 50 sequential LLM calls in the request. Either (a) cap concurrent
  skim runs and stream progress, or (b) for bulk operations, enqueue skims via the same path
  the existing "regenerate all skims" button uses. Pick one before shipping; don't leave it
  ambiguous.
- **Skill-hash invalidation**: `topic_paper_notes.skim_skill_hash` stores the hash of the
  skim skill at generation time. If the topic's skill changes after auto-skim runs, the
  existing UI already shows a "skill changed, regenerate?" affordance — this phase doesn't
  change that, but verify it still works after the migration.

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
- **Migrating inner tab routes** to `/papers/{id}/topics/{tid}/skim` etc. — cosmetic; call
  out as a follow-up. The query-param form keeps working.
- **Dropping `paper_notes.human_note` column** — defer to a cleanup phase once the migration
  has soaked.
- **Per-topic embeddings** — embeddings are paper-level, fine as-is.
- **Auto-deep-synthesis** on add-to-topic — too expensive; deep stays manual.
