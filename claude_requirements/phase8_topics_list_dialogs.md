# Phase 8: Topics List — Dialog-Based Panels

## Goal

Replace in-place popover expansions on the Research Topics list with proper modal dialogs, so the table layout never shifts when viewing detail content.

## Changes

### 1. Problem Statements — Modal Dialog

**Current:** `<details>` popover that drops down inline, shifting table layout.

**New:** Clicking "N statements" opens a `<dialog>` modal containing the numbered list.

- Button triggers `hx-get="/topics/{id}/problem-statements"` into a shared `#modal-container` in `base.html`
- Response renders a `topics/partials/ps_modal.html` partial with the `<dialog open>` element
- Clicking backdrop or a Close button closes the dialog via JS (`dialog.close()`)
- No table layout change

### 2. Skim Skill Editor — Modal Dialog

**Current:** Clicking "✓ Set" / "✗ None" does an inline `outerHTML` swap of the table cell, expanding the editor form in-place (wide textarea, label customizer, file upload) — visually disruptive.

**New:** Clicking "✓ Set" / "✗ None" opens a `<dialog>` modal with the full skill editor form.

- Button triggers `hx-get="/topics/{id}/skills/skim/edit"` into `#modal-container`
- Save / Cancel close the dialog and fire an `hx-get` to refresh the cell link (`skill_cell.html`) without re-rendering the whole row
- Toast notification on save (already implemented)

### 3. Deep Synthesis Skill Editor — Modal Dialog

Same as Skim Skill above, but for `skill_name = "deep"`.

## Implementation Plan

### base.html
- Add `<div id="modal-container"></div>` near `</body>`
- Add shared JS helpers: `openModal()` / `closeModal()` that call `dialog.showModal()` / `dialog.close()`
- Add backdrop-click-to-close handler

### New routes
- `GET /topics/{id}/problem-statements` → renders `ps_modal.html` partial (dialog fragment)
- Reuse existing `/topics/{id}/skills/{skill_name}/edit` → renders `skill_modal.html` partial (dialog fragment)

### New partials
- `topics/partials/ps_modal.html` — `<dialog>` with problem statements list + Close button
- `topics/partials/skill_modal.html` — `<dialog>` wrapping the skill editor form; Save/Cancel close dialog and patch cell

### topic_row.html changes
- Problem statements cell: replace `<details>` with a button using `hx-get` + `hx-target="#modal-container"` + `hx-swap="innerHTML"` + `onclick="openModal()"`
- Skill cells remain as-is (simple link); link updated to target modal instead of cell outerHTML

### skill_cell.html changes
- Link targets `#modal-container` with `hx-swap="innerHTML"` and calls `openModal()` via `hx-on::after-request`

### skill_editor.html changes
- Wrap form in `<dialog>` structure when rendered as modal
- Save (PUT) response: close dialog + patch `#skill-editor-{type}-{id}` cell with updated `skill_cell.html`
- Cancel: close dialog (JS only, no server round-trip needed)

## Out of Scope
- Keywords popover — already works well as a small dropdown, no change needed
