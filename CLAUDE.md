# CLAUDE.md — mbox Viewer

Guidance for extending this project. Read this before changing code.

## What this is

A Dockerized, single-user web viewer for **one large Google Takeout `.mbox` file**
(10GB+ is the design target). It indexes the mbox once into SQLite (with an FTS5
full-text index), then serves a 3-pane web UI: Gmail labels as folders, message
browsing/search, and sanitized message + attachment viewing.

Spec and implementation plan live under `docs/superpowers/`:
- `docs/superpowers/specs/2026-06-07-mbox-viewer-design.md`
- `docs/superpowers/plans/2026-06-07-mbox-viewer.md`

## Architecture

```
host .mbox (read-only)  ──►  indexer (streaming scan)  ──►  SQLite + FTS5 index (writable volume)
                                                                   │
browser ◄── static JS UI ◄── FastAPI ◄── reads metadata/search ────┘
                                  └── reads ONE message by byte offset (seek+read) on demand
```

The mbox is never loaded whole into memory. The indexer records each message's
`(offset, length)`; viewing a message seeks to that offset and reads only its bytes.

## Module map (`src/mboxviewer/`)

| File | Responsibility | Depends on |
|------|----------------|------------|
| `config.py` | `Settings` dataclass + `load_settings()` (env vars) | — |
| `store.py` | ALL SQLite access: schema, writes, queries, FTS search, `clear()`, `savepoint()` | — |
| `reader.py` | mbox bytes → `email.EmailMessage`: `iter_message_spans`, `read_message`, `iter_attachments`, `get_display_body`, `parse_labels` | the mbox file |
| `extract.py` | attachment bytes → text: `extract_text` (pdf/docx/text), `html_to_text` | pypdf, python-docx |
| `sanitize.py` | unsafe email HTML → safe HTML: `sanitize_html` | bleach[css] |
| `status.py` | thread-safe `IndexStatus` progress holder (read by `/api/status`) | — |
| `indexer.py` | orchestrate scan → store: `build_index` (calls `progress(count, bytes_done)`), `index_is_current` | reader, extract, store |
| `api.py` | FastAPI app + routes: `create_app(index_in_background=True)`, `/api/status`, `_render_body`, `_content_disposition(inline=)` | store, reader, sanitize, indexer, status |
| `main.py` | uvicorn entrypoint | config, api |
| `static/` | `index.html`, `style.css`, `app.js` — vanilla JS, no build step | the API |

Keep one responsibility per file. `store.py` is the only place that touches SQLite —
keep it that way.

## Data model (SQLite)

- `messages(id, offset, length, message_id, subject, from_addr, to_addr, date, date_raw)`
  — `date` is **ISO-8601** so lexicographic `ORDER BY date DESC` sorts chronologically.
- `labels(id, name UNIQUE)` + `message_labels(message_id, label_id)` — many-to-many
  (a message can have multiple Gmail labels).
- `attachments(id, message_id, idx, filename, mime, size)`.
- `messages_fts` — **contentless FTS5** (`content=''`) over subject/from/to/body/attachments.
  Insert by `rowid = messages.id`. Empty it with `INSERT INTO messages_fts(messages_fts) VALUES('delete-all')`.
- `meta(key, value)` — stores `source_size` and `source_mtime` for staleness detection.

## Critical contracts (don't break these)

1. **Attachment `idx`** is the walk-order index produced by `reader.iter_attachments`.
   The indexer stores it; the download endpoint re-derives it by walking the same
   function. If you change attachment enumeration, change it in `reader.iter_attachments`
   only — both paths use it, so they stay consistent.
2. **Body rendering is two-path:** HTML parts go through `sanitize_html`; plain-text
   parts go through `html.escape` (NOT the sanitizer) in `api._render_body`. Routing
   plain text through the HTML sanitizer silently drops `<word>` sequences (e.g.
   `List<String>`). Keep them separate.
3. **`sanitize_html` is security-sensitive.** It (a) strips dangerous elements +
   their content with an `HTMLParser`-based stripper that tracks depth and rolls back
   the RAWTEXT/stray-close quirk, (b) runs bleach with a `CSSSanitizer` so inline
   email CSS is preserved, (c) blocks remote `src`/CSS `url()` including
   **protocol-relative** `//host` unless `allow_remote=True`. The body also renders in
   a `sandbox=""` iframe (`srcdoc`) — that sandbox is the primary XSS containment;
   the sanitizer is defense-in-depth + tracking-pixel blocking. Add tests for any new
   bypass class (nested/unclosed tags, new URL forms).
4. **Indexer is all-or-nothing per message and idempotent.** It calls `store.clear()`
   first, wraps each message in `store.savepoint()` (a failed message rolls back fully
   rather than leaving a browsable-but-unsearchable row), commits every `COMMIT_EVERY`
   (2000) to bound WAL growth, and skips malformed messages (logged to stderr). Re-running
   is safe (no duplication).
5. **mbox parsing:** message boundary = a line starting with `From ` that is at file
   start or preceded by a blank line (mboxrd). `read_message` un-escapes mboxrd
   `>From ` → `From ` (removes exactly one `>`). Google Takeout is mboxrd format.
6. **`index_is_current`** compares stored `source_size` + integer `source_mtime`
   against the live file. A changed file triggers a full re-index on next start.
7. **Concurrency model (background indexing).** When the index is stale, `create_app`
   indexes on a **daemon thread** and serves immediately; when current, it serves at
   once. `Store` uses **thread-local connections** (`store.conn` is a per-thread
   property) so the indexer thread (writer) and request threads (readers) never share a
   connection — WAL + `busy_timeout` make concurrent read+write safe, and readers see
   results fill in as the indexer commits. `store.create_schema()` MUST stay
   synchronous in `create_app` before the thread spawns (see the comment there).
   Progress flows `build_index(progress=status.update)` → `IndexStatus` →
   `GET /api/status`; the frontend polls it for the status bar. `IndexStatus.fail`
   (and a `BaseException` guard in `_run_index`) ensure status never sticks at
   `indexing=True`.
8. **Inline attachment disposition is allowlisted.** `GET .../attachments/{idx}?inline=1`
   serves `Content-Disposition: inline` ONLY for `_SAFE_INLINE_MIMES` (PDF + raster
   images) and always sends `X-Content-Type-Options: nosniff`; any other type is forced
   to `attachment`. This prevents a crafted `text/html` attachment from rendering
   same-origin (the PDF preview iframe is NOT sandboxed, so this allowlist is the guard).
   Do not widen the allowlist to script-capable types (`text/html`, `image/svg+xml`).

## Conventions / gotchas

- **Python version:** the local dev venv is **3.9**, the Docker image is **3.12**.
  Keep code 3.9-compatible at runtime — use `typing.Optional[...]`, NOT `X | None`
  in function signatures (FastAPI evaluates annotations and `X | None` raises on 3.9).
- **`src/` layout:** `pytest.ini` sets `pythonpath = src`. Run things with
  `PYTHONPATH=src` outside pytest.
- **Tests are TDD and use real artifacts.** `tests/conftest.py` provides the
  `sample_mbox` fixture, which generates a genuine mbox with real PDF (reportlab) and
  DOCX (python-docx) attachments. Prefer real round-trips over mocks. Every module has
  a matching `tests/test_*.py`; keep that bar when adding code.
- **Index build runs on a background thread** (`create_app(index_in_background=True)`,
  the default); the server serves immediately and `/api/status` reports progress.
  Tests pass `index_in_background=False` to index synchronously for deterministic
  assertions.
- **`Settings`** is a plain dataclass; `create_app(settings)` builds the index if
  stale and stores `store`/`settings` on `app.state`. Routes close over `store`.

## Dev workflow

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                       # full suite (fast; uses tmp dirs)
PYTHONPATH=src MBOX_PATH=/path/to.mbox INDEX_PATH=./index.db \
  .venv/bin/python -m mboxviewer.main   # run locally on :8000
```

Docker: `./run.sh /path/to/your.mbox` (build + run). See `README.md`.

## How to extend (common changes)

- **Support a new attachment type for search:** add a branch in
  `extract.extract_text` (dispatch on mime); add a dependency to `requirements.txt`
  (and `bleach[css]`-style extras if needed); add a test in `test_extract.py`.
- **Add a searchable field:** add the column to `messages_fts` in `store.SCHEMA`,
  populate it in `store.add_fts` + `indexer.build_index`, and include it in
  `_fts_query`/`search`. Re-index (bump nothing — `clear()` runs on every build).
- **Add an API endpoint:** add a route inside `create_app` in `api.py`; add a test in
  `test_api.py` using `TestClient`.
- **Frontend change:** edit `static/app.js` / `style.css` (no build step). Escape ALL
  email-controlled strings injected via `innerHTML` with `escapeHtml` — the main
  document is NOT sandboxed (only the message-body iframe is). Body HTML goes to the
  iframe via `srcdoc`.

## Implemented enhancements (spec: docs/superpowers/specs/2026-06-07-background-indexing-inline-pdf-design.md)

- **Background indexing + on-site progress** — serves immediately; daemon-thread index;
  `GET /api/status`; status bar in `app.js` polls and fills results in live. See
  contracts #7 above.
- **Inline PDF viewing** — `?inline=1` (allowlisted, see contract #8) renders PDFs in a
  reader-pane `<iframe>` (`viewPdf` in `app.js`); download link kept.
- **Durable remote-image archiving** — opt-in `POST /api/archive/start` downloads remote
  images (skipping trackers and SVG/XML) into a separate host-folder archive (`AssetStore`
  / `archive.db` + `assets/`, env `ARCHIVE_DIR`); `message_detail` rewrites cached remote
  `<img>`/CSS `url()` to `/api/asset/{hash}`. Durable across index rebuilds; short-circuits
  on an unchanged mbox. Backup unit = mbox + archive folder; the index is disposable.

## Known follow-ups (deliberately deferred)

- Attachment downloads are buffered (`Response(content=...)`), not streamed — fine for
  Gmail's 25MB attachment cap; stream if larger attachments are expected.
- Indexing progress is in-memory only; an interrupted run re-indexes from scratch (the
  `meta` "done" marker is written only on completion). No cancel/pause controls.
- `/api/status` reports `bytes_total: 0` on a reused (already-current) index — harmless
  (the bar is hidden when ready), but inconsistent if an external consumer reads it.
- Inline preview is PDF-only in the UI (images are allowlisted server-side but the
  frontend never requests them inline). No docx/other preview.
- Single-mbox only. No Maildir / nested-folder / multi-file discovery (out of scope).
- No authentication — assumes single-user local use.
- Search uses prefix-AND of quoted terms (`_fts_query`); no advanced query UI.

## Process note

This was built test-first (each module: failing test → implement → pass → commit) via
subagent-driven development, with spec-compliance + code-quality review per task and a
final holistic review. The end-to-end behavior (labels, PDF/DOCX search, attachment
download, remote-image blocking + toggle, pagination, Docker index reuse) was verified
in a real browser and a running container. Keep that verification bar for new features.
