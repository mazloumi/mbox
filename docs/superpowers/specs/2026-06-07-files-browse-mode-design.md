# Files Browse Mode (browse attachments by type) — Design

**Date:** 2026-06-07
**Status:** Approved

## Goal

Add a **Files** browsing mode alongside the existing **Folders** mode. In Files mode the
left pane lists **file-type categories with counts**; clicking a category lists its
**files** in the middle pane; clicking a file shows its **extracted text** in the reader.
All on-demand — no re-index, no new storage.

## Decisions

- **Mode tabs:** the header gets two tabs, **Folders** and **Files** (active one
  highlighted). Clicking a tab switches the left-pane mode; clicking the already-active
  tab toggles the pane collapse (preserves today's collapse feature). The archive button
  stays in the header.
- **Categorize by MIME only** (refinement from brainstorming): the category is a pure
  function of the attachment's MIME type. This keeps the category counts and the file
  listings perfectly consistent and SQL-pageable. Generic/unknown MIME
  (`application/octet-stream`, empty) → **Other** (no filename-extension fallback).
- **On-demand text:** a file's text is extracted when clicked (read the message by byte
  offset → find the attachment → run the existing PDF/DOCX/text extractor). No new
  storage, no re-index.

## Categories (`filetypes.py`)

`category_for_mime(mime) -> str`, returning one of (display order):
`Documents, Spreadsheets, Presentations, Images, Archives, Calendar, Media, Other`.

Mapping (lowercased mime, `;`-params stripped):
- **Documents:** `application/pdf`; `application/msword`;
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`;
  `application/rtf`; `application/vnd.oasis.opendocument.text`; any `text/*`
  except `text/csv` and `text/calendar`.
- **Spreadsheets:** `application/vnd.ms-excel`;
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`;
  `application/vnd.oasis.opendocument.spreadsheet`; `text/csv`.
- **Presentations:** `application/vnd.ms-powerpoint`;
  `application/vnd.openxmlformats-officedocument.presentationml.presentation`;
  `application/vnd.oasis.opendocument.presentation`.
- **Images:** any `image/*`.
- **Archives:** `application/zip`, `application/x-zip-compressed`, `application/gzip`,
  `application/x-gzip`, `application/x-tar`, `application/x-rar-compressed`,
  `application/vnd.rar`, `application/x-7z-compressed`.
- **Calendar:** `text/calendar`.
- **Media:** any `audio/*` or `video/*`.
- **Other:** everything else.

`CATEGORY_ORDER` is the display order list. Pure module, no SQL.

## Backend

### `store.py`
- `attachment_mime_counts()` → `SELECT mime, COUNT(*) c FROM attachments GROUP BY mime`
  → list of `(mime, count)` rows.
- `list_files_by_mimes(mimes, limit, offset)` →
  `SELECT a.message_id, a.idx, a.filename, a.size, a.mime, m.subject, m.date`
  `FROM attachments a JOIN messages m ON m.id = a.message_id`
  `WHERE a.mime IN (<placeholders>) ORDER BY a.filename LIMIT ? OFFSET ?`.
  Returns `[]` for an empty mime list.

### `api.py`
- `GET /api/filetypes` → from `attachment_mime_counts()`, sum into categories via
  `category_for_mime`; return `[{category, count}]` in `CATEGORY_ORDER`, omitting
  zero-count categories.
- `GET /api/files?category=&page=1&page_size=50` → resolve `category` → the set of mimes
  whose `category_for_mime` equals it (from `attachment_mime_counts()`); then
  `list_files_by_mimes`. Return `{files: [{message_id, idx, filename, size, mime,
  subject, date}], page}`. Unknown/empty category or no matching mimes → `{files: [], page}`.
- `GET /api/files/{message_id}/{idx}/text` → `get_message_row` (404 if none);
  `read_message` (503 on `FileNotFoundError`); iterate `iter_attachments`; on matching
  `idx` return `{filename, mime, size, text}` where `text = extract_text(filename, mime,
  payload)` (`""` when the type isn't text-extractable); 404 if the idx isn't found.
- The existing attachment download route (`/api/messages/{id}/attachments/{idx}`) is the
  Download link.

## Frontend (`static/`)

- **Header:** replace the single `☰ Folders` button with two tabs `#tab-folders` /
  `#tab-files` (a `.active` class marks the current mode). The archive button stays.
- **Mode state:** `browseMode` ∈ `{"folders","files"}`. Clicking the inactive tab
  switches mode, clears the reader, ensures the pane is expanded, and loads that mode's
  left list. Clicking the active tab toggles `folders-collapsed` (the existing collapse,
  persisted).
- **Left pane (`#label-list`):** Folders mode renders labels (today's `loadLabels`);
  Files mode renders categories (`loadCategories`) as rows `CategoryName  count`.
  Selecting a label sets `activeLabel` + loads messages; selecting a category sets
  `activeCategory` + loads files.
- **Middle pane (`#message-list`):** `reload()`/`loadNextPage()` branch on `browseMode` —
  messages (`/api/messages` | `/api/search`) or files (`/api/files?category=`). A file
  row shows the filename, and below it the source email subject + a human size.
- **Reader:** add a `<pre id="reader-text" hidden>`. `openMessage` (folders) shows the
  sanitized body iframe and hides `#reader-text` (today's behavior). `openFile(mid, idx,
  filename, mime, size)` fetches `/api/files/{mid}/{idx}/text`, sets `reader-header` to
  the filename · type · human size · a **Download** link, sets `#reader-text` via
  `textContent` (XSS-safe) to the extracted text — or *"No extractable text for this file
  type."* when empty — shows `#reader-text`, and hides the body/PDF iframes.
- **Search box** is hidden in Files mode (search is message-scoped); shown in Folders mode.
- Arrow-key nav reuses each row's click handler, so it opens messages or files per mode.
- Footer is unchanged.

## Error handling
- `/api/files/{id}/{idx}/text`: 404 (no message / idx), 503 (mbox file gone), `text=""`
  for non-extractable types (image/zip/xlsx) → the UI shows the friendly "no text" message.
- Empty/unknown category → empty file list (no error).
- Extraction errors are already swallowed by `extract_text` (returns `""`).

## Testing
- `filetypes`: `category_for_mime` for pdf→Documents, docx→Documents, text/plain→Documents,
  xlsx/csv→Spreadsheets, pptx→Presentations, image/png→Images, application/zip→Archives,
  text/calendar→Calendar, audio/mpeg→Media, application/octet-stream→Other; `CATEGORY_ORDER`
  contains all returned categories.
- `store`: `attachment_mime_counts` over the `sample_mbox` index (pdf + docx);
  `list_files_by_mimes` returns the attachment rows joined with the message subject/size.
- `api`: `/api/filetypes` returns Documents with count 2 (the sample's pdf + docx);
  `/api/files?category=Documents` lists both with filenames; `/api/files/{id}/{idx}/text`
  returns the PDF's "INVOICE 12345" text and `""` for a non-extractable file.
- Browser: switch to Files mode → categories with counts; click Documents → file list;
  click a file → its extracted text in the reader + a working Download link; mode tabs +
  collapse behave; redeploy and verify on the real mailbox.

## Out of scope (YAGNI)
- xlsx/pptx text extraction (shows "no extractable text" + Download for now).
- Inline image preview in the reader (Download link only; the attachment endpoint already
  supports inline if added later).
- Searching/filtering within Files mode; sorting controls.
- Filename-extension fallback for generic MIME types (octet-stream → Other).
