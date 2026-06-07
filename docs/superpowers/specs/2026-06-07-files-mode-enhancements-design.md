# Files Mode Enhancements — Design

**Date:** 2026-06-07
**Status:** Approved

## Goal

Four improvements to the existing **Files** browse mode:

1. **Link to the containing email** from a file's reader view.
2. **Search in Files mode** — the search box works for files (by **filename and content**).
3. **Image preview** — image files render inline in the reader (not just a Download link).
4. **More text extraction** — add `pptx`, `xlsx`, and `xls` to the on-demand extractor.

All on-demand and additive; no schema change.

## Decisions (from brainstorming)

- **Search = filename OR content.** A file matches if its **filename** contains the query OR
  its **email** matches the existing full-text index (`messages_fts`). Because attachment text
  is folded into the per-message FTS, a content match is at the **email** level: a multi-attachment
  email surfaces all of its files in the active category. Search is scoped to the active category
  when one is selected; with no category selected, a query searches **all** files.
- **Extraction set = `pptx`, `xlsx`, `xls`** via `python-pptx`, `openpyxl`, `xlrd`. Legacy binary
  `ppt`/`pps` have no dependable pure-Python extractor and continue to return `""` ("no extractable
  text"). This is the on-demand reader extractor only (see FTS note below).
- **Email link** opens the containing message: it switches to **Folders** mode and opens that
  message in the reader (the app is a single page with no URL routing).
- **Image preview** reuses the existing inline-attachment endpoint
  (`/api/messages/{id}/attachments/{idx}?inline=1`), which already allowlists raster images and
  sends `X-Content-Type-Options: nosniff` (contract #8). No new server endpoint.

## FTS / content-search note (important)

The full-text index was built with the *previous* extractor, so it contains text for
`pdf`/`docx`/`text` attachments only. **Content search will not find `pptx`/`xlsx`/`xls` by their
text until the index is rebuilt** — but their **filenames** are searchable immediately, and the
**on-demand reader** shows their extracted text regardless. Rebuilding the index (re-running the
indexer) folds the new extractors into the searchable FTS. This deploy does **not** force a
re-index (the index is disposable and the mbox is unchanged); a re-index is an optional follow-up.

## Backend

### `extract.py`
Add three extractors dispatched by MIME in `extract_text` (all wrapped by the existing
try/except → `""` on error):
- `application/vnd.openxmlformats-officedocument.presentationml.presentation` → `python-pptx`:
  concatenate every shape's `text_frame.text` across all slides.
- `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` → `openpyxl`
  (`read_only=True, data_only=True`): tab-join non-empty cells per row, newline-join rows, across
  all sheets.
- `application/vnd.ms-excel` → `xlrd` (`open_workbook(file_contents=data)`): same row/cell join
  across all sheets.

`requirements.txt` gains `python-pptx`, `openpyxl`, `xlrd`. `requirements-dev.txt` gains `xlwt`
(test-only, to synthesize a real `.xls`).

### `store.py`
Extend `list_files_by_mimes(mimes, limit, offset, query=None)`:
- Keep dropping `None` mimes.
- Build a dynamic `WHERE`: the mime filter (when `mimes` non-empty) AND, when `query` is given,
  `(a.filename LIKE ? ESCAPE '\' OR a.message_id IN (SELECT rowid FROM messages_fts WHERE
  messages_fts MATCH ?))`. The `LIKE` pattern is `%` + the query with `\`, `%`, `_` escaped + `%`.
  The `MATCH` argument is `_fts_query(query)`; if it is empty (query had no usable terms), fall
  back to filename `LIKE` only.
- If neither a mime filter nor a query is present, return `[]`.
- Wrap the `execute` in the same `sqlite3.OperationalError` guard `search` uses (return `[]` on
  missing-table / fts5 / syntax errors).

### `api.py` — `GET /api/files`
Make both `category` and `q` optional: `category: Optional[str] = None`, `q: Optional[str] = None`
(plus the existing `page`/`page_size`). Logic:
- `query = (q or "").strip()`.
- If `category`: resolve its mimes (as today). If the category resolves to **no** mimes
  (unknown/empty category) → `{"files": [], "page": page}`.
- If **no** `category` **and no** `query` → `{"files": [], "page": page}` (nothing to list).
- Otherwise call `store.list_files_by_mimes(mimes, page_size, offset, query=query or None)` and
  return the same `{files:[{message_id, idx, filename, size, mime, subject, date}], page}` shape.

No change to `/api/filetypes` (counts stay totals) or `/api/files/{id}/{idx}/text`.

## Frontend (`static/`)

- **index.html:** add `<img id="reader-image" hidden>` to `#reader` (after `#reader-text`).
- **Search box visible in Files mode.** Remove the `searchbar` hide in `setMode`; the box shows in
  both modes. `setMode` resets `currentQuery = ""` and clears the input value when switching modes.
- **`pageUrl` (files branch):** include `category` (when `activeCategory`) and `q` (when
  `currentQuery`). **`reload` guard:** only skip the fetch when files mode has **neither** a
  category **nor** a query.
- **Reader panes are mutually exclusive** across body / pdf / text / **image**. `openMessage`,
  `viewPdf`, `setMode` all hide `#reader-image`.
- **`openFile`** branches on MIME:
  - **image/**\* → show `#reader-image` with `src=/api/messages/{mid}/attachments/{idx}?inline=1`;
    hide body/pdf/text.
  - otherwise → show `#reader-text` with extracted text (today's behavior); hide body/pdf/image.
  - Either way the header shows filename · type · size, and the attachments area shows a
    **Download** link **and** an **Open email** action.
- **Open email:** a global `openEmailFromFile(mid)` (called via inline `onclick`, like `viewPdf`)
  that runs `setMode("folders")` then `openMessage(mid)`.
- **style.css:** `#reader-image { max-width: 100%; max-height: 100%; object-fit: contain; margin:
  auto; }` and ensure it participates in the flex-column reader like the other panes.

## Error handling

- Search with no usable FTS terms → filename `LIKE` only (no error).
- Unknown category → empty list. No category and no query → empty list.
- Image whose inline fetch fails → the browser shows a broken-image icon; the Download link still
  works. (No special handling; acceptable.)
- New extractors throw on a corrupt file → caught by `extract_text` → `""` → "no extractable text".

## Testing

- **extract:** round-trip a generated `.pptx` (python-pptx), `.xlsx` (openpyxl), and `.xls` (xlwt)
  through `extract_text` and assert the embedded text is returned; a corrupt blob returns `""`.
- **store:** `list_files_by_mimes` with `query=` matches by filename (`"invoice"` → `invoice.pdf`)
  and by content (a term in an indexed attachment/body); empty/garbage query behaves; no-mime +
  query searches across all files.
- **api:** `/api/files?category=Documents&q=invoice` → `invoice.pdf`; `/api/files?q=invoice`
  (no category) → `invoice.pdf`; `/api/files` (neither) → `[]`.
- **Browser:** in Files mode the search box is visible and filters files; an image file shows an
  inline preview; a file's reader has a working **Open email** link that switches to Folders and
  opens the message; a `.xlsx`/`.pptx` file shows its extracted text. Redeploy and sanity-check on
  the real mailbox.

## Out of scope (YAGNI)

- Forcing a production re-index (documented as an optional follow-up).
- Legacy binary `ppt`/`pps` extraction.
- Inline PDF preview in Files mode (Download + Folders-mode "View" already cover it).
- Per-attachment FTS granularity (content match stays email-level).
- Highlighting search matches; sorting/filter controls.
