# Background Indexing + Status, and Inline PDF Viewing — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** the mbox viewer (`docs/superpowers/specs/2026-06-07-mbox-viewer-design.md`)

## Goals

Two enhancements to the existing viewer:

1. **Background indexing + on-site status.** Today `create_app` indexes the whole
   mbox synchronously before the server binds, so a 13GB file leaves the site
   unreachable for ~17 minutes on first run. Instead: serve immediately, index on a
   background thread, expose progress via `GET /api/status`, and show a status bar in
   the UI so a user who reloads sees where indexing is at. Browsing and search work on
   the partially-built index and fill in live.

2. **Inline PDF viewing.** Today attachments are download-only. Let PDF attachments
   render inline in the reader pane (the mailbox has ~3,500 PDFs), keeping the
   download link too.

## Constraints / facts

- Single container, single-user, local use (no auth).
- Neither feature changes the SQLite schema or the `meta` keys, so an existing
  completed index is reused after upgrade — **no forced re-index**.
- Verified target data: ~54k messages, ~14k attachments (~3,500 PDFs), 101MB index.

---

## Feature 1 — Background indexing + status

### Startup flow (`api.create_app`)

- Open the store, `create_schema()`.
- Check `index_is_current(settings, store)` (fast `meta` read).
  - **Current** → status = `ready`; no indexing.
  - **Stale/absent** → status = `indexing`; spawn a **daemon thread** that runs the
    index build, then return the app immediately so uvicorn binds and serves at once.
- A new boolean param `create_app(settings, index_in_background=True)` lets tests run
  the build synchronously for deterministic assertions.

### Status object

A small thread-safe holder on `app.state.status` with fields:
`indexing: bool`, `ready: bool`, `messages: int`, `bytes_done: int`,
`bytes_total: int`, `error: str | None`. Updated by the indexer thread, read by
`/api/status`. Guarded by a `threading.Lock` (updates are infrequent — every 500
messages). `percent` is derived as `bytes_done / bytes_total * 100`.

### Indexer progress

`indexer.build_index`'s `progress` callback changes from `progress(count)` to
`progress(count, bytes_done)`, where `bytes_done = offset + length` of the most
recent message. `bytes_total = os.path.getsize(settings.mbox_path)`. The callback
updates the status holder. On an exception escaping the thread, the status records
`error` and sets `indexing=False`, `ready=False`.

### SQLite threading

A single `sqlite3.Connection` must not be shared across threads. Therefore:

- The **indexer thread** uses its **own** `Store`/connection (separate
  `sqlite3.connect` to the same db file).
- **API read handlers** use a **thread-local** connection — each worker thread in
  FastAPI's threadpool gets its own. `Store` gains a thread-local connection
  accessor; `create_schema`/`clear`/index writes happen on the indexer's connection.

WAL mode (already enabled) permits one writer + many readers concurrently, so the
partially-built index is safely readable and grows as the indexer commits (every
`COMMIT_EVERY` = 2,000 messages). `build_index` still calls `store.clear()` first, so
during the first moments the DB is empty and the UI shows zero counts until the first
commit.

### API

`GET /api/status` →
```json
{ "indexing": true, "ready": false, "messages": 21188,
  "bytes_done": 6079170662, "bytes_total": 13457765023,
  "percent": 45.2, "error": null }
```

### Frontend status bar

On load, `app.js` polls `GET /api/status` every ~2s. While `indexing`:
- show a thin top bar: `Indexing… 45% · 21,188 messages`,
- periodically refresh the label list and the current message list/search so results
  fill in live.
When `ready`: do one final refresh, then hide the bar. On `error`: show the message
in the bar (red) and stop polling.

### Testing

- `create_app(..., index_in_background=False)` then assert `/api/status` →
  `ready=True, indexing=False, messages=2, percent=100`.
- Background path: `create_app(...)` (default), poll `/api/status` until `ready`
  with a timeout, assert final counts.
- `build_index` still works with the 2-arg progress callback; existing indexer tests
  (which pass no callback) remain valid.

---

## Feature 2 — Inline PDF viewing

### Backend

- `_content_disposition(filename, inline=False)` gains an `inline` flag: when set, it
  returns `Content-Disposition: inline` (otherwise `attachment`, as today). Non-ASCII
  handling (RFC 5987) is preserved for both.
- The attachment route accepts `GET /api/messages/{id}/attachments/{idx}?inline=1`
  and passes `inline=True` to the helper. `Content-Type` remains the attachment's
  mime (e.g. `application/pdf`), so the browser renders PDFs natively.

### Frontend

- In the reader's attachment row, every attachment keeps its **download** link.
- PDF attachments (`mime === "application/pdf"`) additionally get a **"View"** action
  that toggles an `<iframe>` preview **inside the reader pane**, below the message
  body, with `src="/api/messages/{id}/attachments/{idx}?inline=1"`. Clicking "View"
  again (or opening another message) hides/replaces it.
- This preview iframe is **separate** from the sanitized message-body iframe and
  points at our own same-origin attachment endpoint (acceptable for local single-user
  use; the browser's built-in PDF viewer renders it).
- Non-PDF attachments stay download-only (inline image preview is a trivial future
  add, out of scope here).

### Testing

- Backend: `?inline=1` → `Content-Disposition: inline` and `Content-Type:
  application/pdf`; default (no param) → `Content-Disposition: attachment`.
- `_content_disposition(name, inline=True/False)` unit-tested directly.
- Frontend: verified manually in the browser.

---

## Deployment / verification

- Develop and unit-test entirely on the side; the running 13GB viewer (built image)
  is unaffected by source edits.
- End-to-end verification uses a **separate throwaway container** on a different port
  with a small test mbox, so the live index is never touched.
- After approval + implementation, rebuild the image and restart the real container;
  `index_is_current` matches the existing 101MB index, so it **serves immediately** —
  no re-index — now with the status bar and inline PDF.

## Out of scope (YAGNI)

- Inline preview for non-PDF types (images, docx).
- Persisting indexing progress across restarts (an interrupted index still restarts
  from scratch, as today — the `meta` "done" marker is written only on completion).
- Cancel/pause indexing controls; multi-file or incremental indexing.
