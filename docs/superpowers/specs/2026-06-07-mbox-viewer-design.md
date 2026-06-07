# Dockerized Gmail-Takeout mbox Viewer — Design

**Date:** 2026-06-07
**Status:** Approved (Approach A)

## Goal

A browser-based viewer for a single large Google Takeout `.mbox` file. Runs inside
Docker; the user mounts a host folder containing the mbox file into the container.
In the browser the user can:

- Browse "folders" (Gmail labels, derived from the `X-Gmail-Labels` header)
- List and read individual emails (sanitized HTML + plain text)
- View and download attachments
- Full-text search across subject, sender, recipients, body, **and extracted
  attachment text**

## Constraints & Key Facts

- Source data: a **single** Google Takeout mbox file, **10GB+**.
- Folders in the UI come from the `X-Gmail-Labels` header (Inbox, Sent, custom
  labels, etc.) as *virtual* folders. A message can appear under multiple labels.
- Search is **full-text including attachment contents**.
- Stack: **Python (FastAPI)**, SQLite FTS5, static vanilla-JS frontend (no Node
  build step). "Just works" via `docker compose up`.
- The source mbox is mounted **read-only**; never modified.

## Chosen Approach (A)

One-time streaming index into SQLite, FastAPI backend, static JS frontend, single
Docker image.

Rejected alternatives:
- **B — on-the-fly parsing, no index:** at 10GB+, every search/label listing would
  re-scan the whole file. Unusable.
- **C — external search engine (Meilisearch/Elasticsearch):** extra container,
  more RAM and ops complexity. Overkill for a single-user local viewer.

## Architecture

Single Docker image running a FastAPI server. Two mounts via `docker-compose`:

- Host mbox file → mounted **read-only** into the container.
- A named volume (or host dir) for the **SQLite index** (writable, persists across
  restarts).

On startup the server checks whether a valid index exists for the mounted file
(matched by path + size + mtime). If not, it runs the **indexer** once (with a
progress log), then serves. If a valid index exists, it serves immediately.

## Components

Each component has one clear purpose and is independently testable.

1. **Indexer** — streams the mbox, splitting on `From ` separator lines, recording
   each message's `(offset, length)`. For each message it parses headers (subject,
   from, to, date, `X-Gmail-Labels`), extracts body text, and extracts text from
   attachments, writing all of it to SQLite + FTS5. One-time pass with progress
   logging. Malformed messages are logged and skipped, never aborting the pass.
   - *Depends on:* Store, Mbox reader, Attachment text extractor.

2. **Store (SQLite)** — schema:
   - `messages` (id, offset, length, subject, from_addr, to_addr, date, ...)
   - `labels` (id, name) and `message_labels` (message_id, label_id) — many-to-many,
     since a message can carry multiple Gmail labels.
   - `attachments` (id, message_id, filename, mime, size)
   - `messages_fts` — FTS5 virtual table over subject/from/to/body/attachment-text.
   - WAL mode so reads can serve while/after indexing.
   - A `meta` table records the indexed file's path/size/mtime to detect staleness.
   - *Depends on:* nothing (pure storage layer).

3. **Mbox reader** — given `(offset, length)`, seeks and reads only that message,
   parses it with the stdlib `email` module, decodes MIME parts. Low memory even on
   a 10GB+ file.
   - *Depends on:* the mounted mbox file path.

4. **Attachment text extractor** — pluggable by MIME type: PDF (`pypdf`), Word
   (`python-docx`), plain text/csv, graceful skip for unsupported types. No
   heavyweight Tika, to keep the image lean (can be added later).
   - *Depends on:* nothing (pure functions over bytes).

5. **API (FastAPI)**
   - `GET /labels` — labels with message counts.
   - `GET /messages?label=&page=` — paginated message list for a label.
   - `GET /messages/{id}` — headers + sanitized HTML body + attachment list.
   - `GET /messages/{id}/attachments/{n}` — streamed attachment download.
   - `GET /search?q=&label=` — FTS-ranked message list (optionally scoped to a label).
   - *Depends on:* Store, Mbox reader, sanitizer.

6. **Frontend** — classic 3-pane web UI (labels sidebar | message list | reader)
   plus a search box. Static files (vanilla JS + `fetch`), no Node build. HTML
   email bodies render in a **sandboxed iframe**.
   - *Depends on:* API.

7. **Security / sanitization** — email HTML is sanitized (`bleach`); remote content
   (tracking pixels/images) is blocked by default, with an optional "load remote
   images" toggle per message.

## Data Flow

- **First run:** mbox → indexer → SQLite.
- **Browsing:** UI → API → SQLite (metadata only).
- **Viewing:** UI → API → SQLite (lookup offset) → mbox reader (seek + read one
  message) → sanitize → render.
- **Search:** UI → API → FTS5 → ranked message list.

## Error Handling

- Missing/inaccessible mbox → clear startup error.
- Malformed/garbled messages → indexer logs and skips; pass continues.
- Source file changed (size/mtime differs from `meta`) → re-index.
- Index stored separately from the source; source mbox stays read-only and untouched.

## Testing

Unit tests per component using small fixture mbox files (a few messages with
labels, an HTML body, a PDF + a docx attachment):

- Indexer records correct byte offsets/lengths.
- Label grouping correct, including a message with multiple labels.
- FTS finds matches in body text **and** in extracted attachment text.
- Mbox reader returns the correct message by offset.
- Attachment extraction returns expected text for PDF/docx/plain.
- HTML sanitization strips scripts and blocks remote content by default.

## Deployment

`docker-compose.yml` with one service:

- Read-only mount of the host mbox file.
- Writable volume for the SQLite index.
- Exposed port (default `8000`).

Usage: set the mbox path (via `.env` or compose), `docker compose up`, open
`http://localhost:8000`.

## Out of Scope (YAGNI for v1)

- Editing, deleting, or replying to mail (read-only viewer).
- Multiple mbox files / Maildir / nested-folder discovery.
- OCR of image attachments; Tika-based extraction of exotic formats.
- Authentication / multi-user (assumed single-user local use).
