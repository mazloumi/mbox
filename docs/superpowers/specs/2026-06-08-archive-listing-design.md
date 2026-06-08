# Archive Listing + Index Inner Filenames — Design

**Date:** 2026-06-08
**Status:** Approved

## Goal

When a user opens a **zip/archive** attachment, list the files inside it (name + size) in the reader
(main panel), and **index the inner filenames** so they're full-text searchable.

## Decisions

- **Formats (stdlib, no new deps):** zip (incl. `.jar`/`.war`/`.ear`) via `zipfile`, and the tar
  family (`.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`) via `tarfile`. **rar/7z are out of scope** (need
  `unrar`/`py7zr` for the ~handful present) — deferred.
- **Listing = extracted text → table.** `extract_text` returns a tab-separated `Name\tSize` listing
  for archives. This (a) folds the inner filenames into the FTS index (searchable, like TNEF inner
  names) and (b) is rendered as a **table** in the reader by the existing CSV/spreadsheet table path —
  consistent UX, minimal new code. **List only** (no per-entry download — not requested).
- **Detect by MIME *or* extension.** Many archives arrive as `application/octet-stream` with a `.zip`
  extension (same generic-MIME problem as categorization), so `extract_text` keys archives on the
  filename extension too.
- **Re-index required** to fold the new listings into FTS → bump `SCHEMA_VERSION` (2 → 3); the
  schema-version guard then re-indexes automatically on deploy. (First real use of that guard.)

## Security
- **Zip listing reads the central directory only** (`ZipFile.infolist()`) — no decompression, so no
  zip-bomb risk; inner names are only displayed (escaped in the table), never written to disk, so
  path-traversal names are harmless.
- **Tar listing** must decompress sequentially to read headers; bounded by a hard **entry cap**
  (5000) and the existing `extract_text` try/except (→ `""`). The mailbox is the user's own export
  (non-adversarial), so a decompression bomb is a low/accepted risk; the cap limits it.
- Inner names render via the table cells' `escapeHtml` (non-sandboxed document).

## Backend (`extract.py`)
- `_ARCHIVE_MIMES` = `{application/zip, application/x-zip-compressed, application/java-archive,
  application/gzip, application/x-gzip, application/x-tar, application/x-gtar,
  application/x-bzip-compressed-tar}`.
- `_ARCHIVE_EXTS` = `{.zip, .jar, .war, .ear, .tar, .tgz, .tbz2}` plus the compound endings
  `.tar.gz`, `.tar.bz2` (checked via `endswith`).
- `_is_archive(mime, filename)` → mime in the set OR filename matches an archive extension.
- `iter_archive_entries(data, cap=5000) -> [(name, size)]`: if `zipfile.is_zipfile` → non-dir
  `infolist()` entries; else try `tarfile.open(fileobj=...)` → `isfile()` members (cap, swallow
  errors → `[]`).
- `_archive_text(data) -> str`: `"Name\tSize"` header + one `name\thuman_size` line per entry;
  `""` when there are no entries.
- Dispatch in `extract_text` (inside the try, before the generic text branches):
  `if _is_archive(mime, filename): return _archive_text(data)`.

## Frontend (`static/app.js`)
- `_ARCHIVE_MIMES` set + `isArchive(m, name)` (mime or `.zip/.jar/.war/.ear/.tar/.gz/.tgz/.bz2/.tbz2`
  extension).
- In `openFile`, add an **archive branch** after the spreadsheet branch and before the `ms-tnef`
  branch: `showOnlyPane(readerTable)`, fetch `/api/files/{mid}/{idx}/text`, and
  `renderTableRows(parseTsv(text))` (same as the spreadsheet path; cells already `escapeHtml`'d).
  Empty → "No files listed."

## Store
- `SCHEMA_VERSION = 3` (was 2). No DDL change.

## Testing
- **extract:** build a real `.zip` (zipfile) with nested paths → `_archive_text` lists each name with
  a size and the header; build a `.tar.gz` (tarfile) → lists its entries; a non-archive blob → `""`;
  `_is_archive("application/octet-stream", "x.zip")` is True (extension path); a directory-only zip →
  `""`. Confirm the inner names appear in the returned text (so FTS will index them).
- **store/indexer:** after `build_index` on a sample whose message has a zip attachment containing
  `secret_plans.txt`, `store.search("secret_plans", None, 10, 0)` finds that message (inner filename
  indexed). `SCHEMA_VERSION` bump → `index_is_current` False against a v2-stamped index.
- **Browser:** clicking a `.zip` shows a Name/Size table of its contents; searching an inner filename
  finds the email.
- **Container e2e:** a real `.zip`/`.jar` lists its contents; after the guard-driven re-index, a known
  inner filename is searchable.

## Out of scope (YAGNI)
- rar / 7z / dmg listing (heavier deps).
- Downloading or previewing individual entries inside an archive.
- Recursing into nested archives (a zip within a zip lists the inner zip as one entry).
