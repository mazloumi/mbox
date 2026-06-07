# UI: Compact Layout, Collapsible Folders, No Broken Images, Keyboard Nav — Design

**Date:** 2026-06-07
**Status:** Approved
**Scope:** Frontend only (`static/index.html`, `static/app.js`, `static/style.css`). No backend/API changes. The 60 backend tests are unaffected.

## Goals

Four UX refinements to the viewer:

1. **Compact folders + message list** — smaller fonts and tighter row padding so more
   folders/emails fit on screen. The reader (email body) keeps its readable size.
2. **Collapsible folder column** — a toggle to hide the Folders column, giving the
   message list and reader more room; choice persists across reloads.
3. **No broken-image icons for blocked remote images** — when remote images are
   blocked (default), they render as nothing (text only) instead of broken-image
   icons; clicking "Load remote images" reveals them.
4. **Arrow-key email navigation** — `ArrowDown`/`ArrowUp` move between emails in the
   list and open them.

## Design

### 1. Compact layout (`style.css`)
- `#labels h2` → ~11px.
- `#label-list li, #message-list li` → padding `5px 10px` (from `8px 12px`), font-size ~12px.
- `.subject` → font-size ~12.5px; `.meta` → ~11px.
- Reader header/body unchanged.

### 2. Collapsible folders (`index.html` + `app.js` + `style.css`)
- Add a slim top toolbar with a toggle button `☰` (id `toggle-folders`).
- Toggling adds/removes a `folders-collapsed` class on `#app`:
  - default: `grid-template-columns: 220px 360px 1fr`.
  - collapsed: `#app.folders-collapsed { grid-template-columns: 360px 1fr }` and
    `#app.folders-collapsed #labels { display: none }` (the hidden aside leaves grid
    flow, so list + reader fill the two columns).
- Persist with `localStorage["foldersCollapsed"]` (`"1"`/`"0"`), applied on load,
  wrapped in try/catch (private-mode safe). The `☰` button stays visible when
  collapsed to re-expand.

### 3. No broken-image icon (`app.js`)
- The backend already blanks blocked remote image `src` to `""`. Prepend a small
  style to the body iframe content so empty/missing-src images don't render:
  `readerBody.srcdoc = '<style>img[src=""],img:not([src]){display:none}</style>' + m.body_html;`
- The style tag renders inside the `sandbox=""` iframe (styles allowed, scripts not).
- "Load remote images" re-fetches with `allow_remote=true`; real `src` values are
  non-empty so the rule does not hide them — images appear.
- Out of scope: `cid:` embedded inline images (we don't serve those; the user's ask
  is remote images).

### 4. Arrow-key navigation (`app.js`)
- A `document` `keydown` listener for `ArrowDown`/`ArrowUp`:
  - Ignore when focus is in an `INPUT`/`TEXTAREA` (don't hijack search typing).
  - Collect message rows = `#message-list li` excluding the `#load-more` row.
  - Find the current `li.active`; move index ±1 (clamped to `[0, len-1]`); if none
    selected, select the first.
  - `target.click()` (reuses the row's existing handler → `setActive` + `openMessage`),
    `preventDefault()` to stop page scroll, and `scrollIntoView({block:"nearest"})`.
- Works across the currently loaded page; at the bottom it stops at the last loaded
  row ("Load more" still works by click).

## Testing / verification

No backend logic changes, so verification is in a real browser against the running
viewer (rebuilt to pick up the static assets), checking each behavior:
- Compact rows render; reader body still readable.
- `☰` hides/restores the Folders column and the state survives a reload.
- An email with blocked remote images shows text with no broken-image icons, and
  "Load remote images" reveals the images.
- `ArrowDown`/`ArrowUp` step through and open emails; typing in search is unaffected.
- `pytest` stays 60/60 (untouched).

## Out of scope (YAGNI)
- Inline preview / un-breaking of `cid:` embedded images.
- Remembering the selected email or folder across reloads.
- Vim-style (j/k) keys; multi-select; drag-to-resize columns.
