# Category Extension-Fallback, Legacy PPT, Spreadsheet Table, Sticky Search, WMA message — Design

**Date:** 2026-06-08
**Status:** Approved

## Goal

Five improvements, driven by a diagnosis of the real mailbox:

1. **Fix the "Other" category** — ~1,100+ attachments (802 `.pdf`, 87 `.doc`, 50 `.jpg`, 22 `.m4a`,
   17 `.pps`, 16 `.ics`, 10 `.xls`, 8 `.wmv`, …) are misfiled because their MIME is generic/garbled
   (`application/octet-stream`, `force-download`, `x-pdf`, even a typo'd `pplication/json`).
   Add a **filename-extension fallback** so categorization uses the extension when MIME is unhelpful.
2. **Legacy `.ppt`/`.pps` text extraction** via `catppt` (the `catdoc` Debian package).
3. **Spreadsheet table view** — `.xls`/`.xlsx` render as a real scrollable table in the reader
   (reusing the CSV table renderer), not just tab-separated text.
4. **Sticky search field** — the search box stays visible while the message list scrolls.
5. **WMA/WMV "can't play" message** — these are browser-undecodable; show a clear note + Download
   instead of a dead player.

## Diagnosis (why "Other" is full)

The original Files design categorized **by MIME only** (deliberately, for consistent SQL-pageable
counts). But many senders attach files with a generic content-type, so the true type — obvious from
the extension — is ignored. Of 3,386 "Other" attachments: 2,001 are `.dat` (winmail.dat, correctly
Other and now viewable), ~113 `.p7s` (signatures, correctly Other), and the rest are mislabeled
documents/images/media/spreadsheets/presentations/calendars/contacts.

## Decisions

- **Extension fallback is applied only when MIME categorization yields "Other"** (the dominant
  octet-stream case). A file whose MIME maps to a real category keeps it; the rare mislabel of a
  *specific* wrong MIME (e.g. `image/pdf`, 2 files) is out of scope.
- **Categorization now depends on (mime, filename), so it is computed at index time and stored** in a
  new `attachments.category` column. `/api/filetypes` counts and `/api/files` listing use the column
  (clean GROUP BY / WHERE, keeps search + pagination). The index is disposable → re-index regenerates
  it. (No frontend category-API change: same `category` param + same category names.)
- **Light previews only** (per product decision): slides → text, spreadsheets → table. No LibreOffice.
- **WMA/WMV → Download-only + message** (per product decision): no ffmpeg transcoding.

## Backend

### `filetypes.py`
- `_EXT_CATEGORY`: map common extensions → category:
  - Documents: `.pdf .doc .docx .rtf .odt .txt .md .pages .docm`
  - Spreadsheets: `.xls .xlsx .ods .numbers .xlsm`
  - Presentations: `.ppt .pptx .pps .ppsx .odp .key .pptm`
  - Images: `.jpg .jpeg .png .gif .bmp .webp .tif .tiff .heic .heif`
  - Archives: `.zip .rar .7z .gz .tar .bz2 .tgz .jar`
  - Calendar: `.ics .vcs`
  - Contacts: `.vcf`
  - Media: `.mp3 .m4a .wav .aac .ogg .oga .flac .opus .wma .mp4 .m4v .mov .avi .wmv .mkv .webm .mpg .mpeg .3gp`
- `category_for(mime, filename) -> str`: `cat = category_for_mime(mime); if cat != "Other": return cat;`
  then `ext = os.path.splitext(filename)[1].lower(); return _EXT_CATEGORY.get(ext, "Other")`.
- `category_for_mime` is unchanged (still used as the first pass).

### `store.py`
- **Schema:** add `category TEXT` to the `attachments` table. `create_schema` runs a defensive
  migration: `ALTER TABLE attachments ADD COLUMN category TEXT` guarded by `OperationalError`
  (no-op if the column exists). (Fresh index — the normal path since we clear before re-index —
  gets it from the CREATE.)
- `add_attachment(message_id, idx, filename, mime, size, category)` — store the category.
- Replace `attachment_mime_counts()` with `attachment_category_counts()` →
  `SELECT category, COUNT(*) c FROM attachments GROUP BY category`.
- Replace `list_files_by_mimes(mimes, …)` with `list_files_by_category(category, limit, offset,
  query=None)` — `WHERE category = ?` (when given) AND the existing filename-`LIKE` OR `messages_fts`
  search; `category=None` + query → global search; neither → `[]`. Keep the `OperationalError` guard.

### `indexer.py`
Compute `category = filetypes.category_for(mime, filename)` in the attachment loop and pass it to
`store.add_attachment(...)`.

### `api.py`
- `/api/filetypes` → from `attachment_category_counts()`, return `[{category, count}]` in
  `CATEGORY_ORDER` (omit zero) — no per-mime aggregation needed anymore.
- `/api/files?category=&q=&page=&page_size=` → `list_files_by_category(category or None, …, query)`.
  Unknown category → empty (the GROUP BY simply has no such bucket). No-category + no-query → empty.

### `extract.py` + `Dockerfile`
- `_ppt_text(data)`: shell out to `catppt` on a temp file (same pattern as `_antiword_text`:
  `shutil.which`, list-argv, timeout, temp cleanup, swallow errors → `""`). Dispatch
  `application/vnd.ms-powerpoint` and `application/mspowerpoint` → `_ppt_text`. (`.pps` shares the
  `vnd.ms-powerpoint` MIME; extension-fallback also routes odd-MIME `.ppt/.pps` here via category, but
  extraction keys on MIME — so also accept by checking the dispatch for the ppt MIMEs.)
- `Dockerfile`: `apt-get install -y --no-install-recommends antiword catdoc` (catdoc provides
  `catppt`). catppt is absent in the dev venv → `_ppt_text` returns `""` locally (tested), real
  extraction verified in the container.

## Frontend (`static/`)

### Sticky search (`style.css`)
Make `#searchbar` `position: sticky; top: 0; z-index: 2;` with a solid background and a bottom border
so it stays pinned while `#message-list` scrolls. (Verify `#list` is the scroll container; if the
whole pane scrolls, pin within it.)

### Spreadsheet table view (`app.js`)
- Refactor the CSV renderer into `renderTableRows(rows, note)` (escaped `<table>`, 500-row cap).
- `parseCsv(text)` stays for CSV; add `parseTsv(text)` = `text.split("\n").map(l => l.split("\t"))`.
- `openFile` dispatch: a **spreadsheet** file (MIME in a small xls/xlsx/ods set, OR filename ends
  `.xls/.xlsx/.ods/.xlsm`) → `showOnlyPane(readerTable)`, fetch `/text`, `renderTableRows(parseTsv(text))`
  (the xls/xlsx extractor already emits tab-separated rows). CSV branch unchanged.

### WMA/WMV message (`app.js`)
- `UNPLAYABLE` = mimes `{audio/x-ms-wma, video/x-ms-wmv, video/x-ms-asf, audio/x-ms-wax}` /
  extensions `{.wma, .wmv, .asf}`. In `openFile`, before building an audio/video player, if the file
  is `UNPLAYABLE` → `showOnlyPane(readerText)`; `readerText.textContent = "This format (Windows Media)
  can't be played in the browser. Use the Download link above."`; return (Download already set).
- For any *other* audio/video, add an `onerror` handler to the element that, on decode failure, swaps
  the pane to the same "can't play — use Download" message (covers odd codecs gracefully).

## Security
- No new inline-serving types; the spreadsheet/PPT text goes through `/api/files/.../text` (JSON) and
  renders via `textContent` (PPT) or escaped table cells (spreadsheet). catppt runs sandboxed (temp
  file, list-argv, timeout) with no email-controlled data on the command line.

## Testing
- **filetypes:** `category_for("application/octet-stream", "x.pdf")` → Documents; `("application/x-pdf",
  "x.pdf")` → Documents; `("application/octet-stream", "s.xlsx")` → Spreadsheets; `("application/
  octet-stream", "d.pps")` → Presentations; `("application/octet-stream", "a.mp3")` → Media;
  `("application/octet-stream", "c.ics")` → Calendar; `("application/octet-stream", "k.vcf")` →
  Contacts; a real MIME still wins (`("application/pdf","x.bin")` → Documents); unknown ext + octet →
  Other.
- **store:** after `build_index` on a sample with an octet-stream-`.pdf` attachment,
  `attachment_category_counts()` shows it under Documents; `list_files_by_category("Documents", …)`
  lists it; `list_files_by_category("Documents", …, query="invoice")` filters; the migration adds the
  column to a pre-existing column-less DB without data loss.
- **api:** `/api/filetypes` buckets by the stored category; `/api/files?category=Documents` includes
  the octet-stream `.pdf`; `?q=` search still works; unknown category → `[]`.
- **extract:** `_ppt_text` returns `""` when catppt is absent (local); the ppt MIME dispatches to it.
- **Browser:** an `.xls`/`.xlsx` renders as a table; the search box stays pinned on scroll; a `.wmv`
  shows the "can't play" message + Download; a (previously-Other) octet-stream `.pdf` appears under
  Documents.
- **Container e2e:** real octet-stream PDFs now under Documents; a real `.ppt` extracts via catppt; a
  real `.xls` shows a table; Other shrinks to ≈ winmail.dat + signatures + genuine unknowns.

## FTS note
Categorization is stored at index time and `.ppt` text needs the new extractor → a **re-index** is
required for this feature (schema change + new extraction). Done at the end (Task 6).

## Out of scope (YAGNI)
- LibreOffice rendered previews; ffmpeg transcoding (both declined).
- Overriding a *specific* wrong MIME by extension (only the →Other case is handled).
- A dedicated category for winmail.dat or signatures.
- Re-categorizing inside TNEF inner files.
