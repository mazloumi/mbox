# Sticky Header + Footer Status Bar — Design

**Date:** 2026-06-07
**Status:** Approved
**Scope:** Frontend restructure (`static/index.html`, `static/app.js`, `static/style.css`)
plus small additive fields on two existing API routes. No new modules.

## Goal

Reorganize the viewer chrome into a **sticky top header** (logo + name + the Folders and
Archive buttons) and a **sticky bottom footer status bar** that always shows the mbox
file name, the index state (fully indexed vs. changed), and the archive state — with the
archive state read from the **persisted** archive metadata so it shows on load without
clicking "Archive remote images".

## Layout

The page becomes a flex column: `#header` (auto height, pinned top) · `#app` (flex:1, the
existing 3-pane grid; the panes scroll internally) · `#footer` (auto height, pinned
bottom). Because the page itself doesn't scroll (only the panes do), the header and footer
stay fixed without needing `position: sticky`.

```
#header:  📬 mbox viewer                 [ ☰ Folders ] [ Archive remote images ]
#app:     Folders | message list (+search) | reader / PDF        (panes scroll)
#footer:  📁 your-mail.mbox · Indexed 54,183 messages · Images: 9,830 archived ✓
```

The old top `#status-bar` and `#toolbar` elements are removed; their content moves into
the header (buttons) and footer (index/archive status text).

## Components

### Header (`#header`)
- Left: a text/emoji logo — `📬 mbox viewer` (no image asset).
- Right: the existing `#toggle-folders` (`☰ Folders`) and `#archive-images`
  (`Archive remote images`) buttons, unchanged handlers (the archive confirm dialog and
  POST stay as-is).

### Footer (`#footer`) — three dot-separated segments
1. **mbox file:** `📁 <mbox basename>` (from `/api/status.mbox`).
2. **Index state** (from `/api/status`):
   - indexing: `Indexing… {percent}% · {messages} messages`
   - ready & current: `Indexed {messages} messages`
   - ready & not current: `⚠ Source changed — restart to re-index`
   - error: `Indexing failed: {error}`
3. **Archive state** (from `/api/archive/status`):
   - running: `Archiving images… {messages_scanned}/{total_messages} · {downloaded} saved · {skipped} skipped · {failed} failed`
   - idle, persisted from `archive.db` — show the full breakdown:
     - `total == 0` → `Images: not archived yet`
     - otherwise → `Images: {total} total · {ok} archived · {skipped} skipped · {failed} failed`,
       suffixed with ` ✓` when `up_to_date` (meta matches current mbox AND `failed == 0`),
       or ` · click to update` otherwise.
   - error: `Archive failed: {error}`

`#footer.error` styling applies when an index or archive error is shown.

## Backend additions (additive only)

### `GET /api/status`
Add to the returned dict (computed in the route, merged onto the existing
`IndexStatus.snapshot()`):
- `mbox`: `os.path.basename(settings.mbox_path)`.
- `current`: `index_is_current(settings, store)` (live; true when the index matches the
  mbox file's size/mtime).

### `GET /api/archive/status`
Add to the returned dict (merged onto `ArchiveStatus.snapshot()`):
- `archived`: `asset_store.asset_counts()` → `{ok, skipped, failed, total}` (persisted).
- `up_to_date`: true when `asset_store.get_archive_meta()` matches the current mbox
  `size`+`int(mtime)` AND `asset_counts()["failed"] == 0`.

These are pure reads; no new state. The frontend uses them to render the footer (and to
show the archived state on first load without triggering a run).

## Frontend changes

- `index.html`: replace the `#status-bar` + `#toolbar` block with `#header` (logo +
  buttons) and add `#footer` (three `<span>` segments: `#mbox-name`, `#index-state`,
  `#archive-state`) after `#app`.
- `app.js`:
  - `pollStatus` writes the index segment (`#index-state`) and the mbox name
    (`#mbox-name`) instead of the old `#status-bar`; still polls every 2s while indexing
    and refreshes labels/list as today.
  - `pollArchive` writes the archive segment (`#archive-state`) using the new
    `archived`/`up_to_date` fields so the persisted state shows on load.
  - The `#toggle-folders` and `#archive-images` handlers are unchanged (the elements just
    live in the header now).
- `style.css`: body flex column; `#header`/`#footer` bars; `#app` becomes `flex:1;
  min-height:0` (the panes already scroll). Footer segments separated by `·`.

## Testing

- Backend: `/api/status` includes `mbox` (basename) and `current` (true after a sync
  index; false after `os.utime` changes the mbox mtime); `/api/archive/status` includes
  `archived` counts and `up_to_date` (false when nothing archived / present after an
  archive run with matching meta). Existing 87 tests stay green.
- Frontend: verified in a real browser against the running viewer — header pinned with
  logo + the two buttons; footer pinned showing the mbox filename, the index state, and
  the **persisted** archive state on a fresh page load (no click needed); the buttons
  still work (folders collapse, archive confirm/progress).

## Out of scope (YAGNI)
- An image/SVG logo file (emoji + text only).
- Clickable footer segments / a separate "re-index now" button (the message says to
  restart; re-indexing is a startup concern).
- Mobile/responsive layout.
