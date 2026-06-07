# Remote Image Archiving (durable offline image cache) — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** the mbox viewer + background-indexing/inline-PDF work.

## Goal

Let the user **archive remote images** referenced by emails so they display offline,
independent of the original hosts. Archiving is **opt-in** (a UI button), **skips
tracking pixels** (never fetched), and once done, cached images **display
automatically** with no view-time network call. The archive is **durable**: it lives
separately from the (disposable) search index, so the user can drop/rebuild the index —
or delete the originals in Gmail — without losing or re-downloading images.

## Durability model (key decision)

Two independent stores, because they have opposite reproducibility:

| Store | Contents | Reproducible? | Location |
|---|---|---|---|
| **Index** | messages/labels/FTS | Yes — rebuild from the mbox anytime | `/index` volume (disposable) |
| **Archive** | image bytes + their metadata | **No** — only re-creatable via the network | a **host folder** (durable) |

- The **archive** is a host directory (default co-located with the mbox so the durable
  set lives together), bind-mounted to `/archive` in the container. It contains
  `archive.db` (SQLite: `assets` + `archive_meta`) and `assets/<url_hash>` (image bytes).
- Keyed only by **URL hash** → independent of message IDs, so it survives an index
  rebuild: the same emails reference the same URLs → same hashes → existing bytes match.
- **A complete offline copy = mbox file + archive folder.** The index is a throwaway
  cache. The user backs up those two, deletes Gmail freely, and can rebuild the index
  or move machines with everything still rendering offline.

## Why opt-in / privacy

Fetching a remote image signals the sender that the email was opened (IP + time), so
bulk-archiving must be a conscious action. Mitigations: opt-in only; tracking pixels are
classified and recorded as `skipped` **before any fetch** (no signal sent); the
downloader honors `HTTP_PROXY`/`HTTPS_PROXY` env for VPN/proxy routing. Viewing cached
images afterward is private (served locally).

## No re-archiving when the mbox is unchanged

`archive_meta` records the mbox `source_size` + `source_mtime` at the end of a full
pass. On a new run, if those match the current mbox **and** there are no `failed`
assets, the worker **short-circuits** (no re-scan, no re-download) and reports the
existing counts. If the mbox changed or some fetches failed, it does a full pass and
picks up only what's new/failed (the per-URL `ok`/`skipped` rows make it resumable).
So: drop the index, rebuild it from the mbox, re-run archive → instant no-op; images
still render from the intact archive.

## Components

### Archive store (`assetstore.py`, new) — owns `archive.db`
Mirrors `Store`'s thread-local-connection pattern, pointed at `<archive_dir>/archive.db`.
- Schema: `assets(url_hash PK, url, content_type, size, width, height, status, error,
  fetched_at)` with `status ∈ {ok, skipped, failed}`; `archive_meta(key, value)`.
- Methods: `create_schema`, `commit`, `upsert_asset(...)`, `get_asset(h)`,
  `asset_status(h)`, `cached_asset_hashes(hashes)→set`, `asset_counts()→
  {ok,skipped,failed,total}`, `get_archive_meta()→{source_size,source_mtime}|None`,
  `set_archive_meta(size, mtime)`.

### `assets.py` (pure, no SQLite)
- `url_hash(url)` (sha256 hex), `normalize_url(url)` (protocol-relative `//h` → `https://h`).
- `extract_image_refs(html) → [(url, width, height)]` for remote `<img>` + CSS `url()`;
  ignores `cid:`/`data:`.
- `is_tracking_pixel(url, width, height)` → declared `width≤2`/`height≤2`, or host
  matches a small built-in tracker-substring list. Heuristic (accepted).
- `fetch_image(url) → FetchResult` — GET, 10s timeout, image/* content-type check,
  size cap (10 MB), honors proxy env, never raises.
- Byte cache I/O keyed by archive dir: `assets_dir(archive_dir)`,
  `asset_path(archive_dir, h)`, `write_asset_bytes(archive_dir, h, data)`,
  `read_asset_bytes(archive_dir, h)`.
- `rewrite_cached_images(html, cached_hashes) → html` — replaces remote `<img src>` and
  CSS `url()` whose `url_hash` is cached with `/api/asset/<hash>`; leaves others alone.

### Archive worker (`archive.py`)
- `ArchiveStatus` — thread-safe progress: `{running, messages_scanned, total_messages,
  urls_seen, downloaded, skipped, failed, error}`; plus `complete_from_counts(counts)`
  for the short-circuit path.
- `run_archive(settings, store, asset_store, status)`:
  1. Short-circuit check (mbox size/mtime vs `archive_meta`, and `failed==0`).
  2. Else: scan every message (`store.all_message_spans()` → `reader.read_message` by
     offset → `get_display_body` → `extract_image_refs`), dedup by `url_hash`, record
     trackers as `skipped` (never fetched), collect the rest.
  3. Download the rest via a bounded `ThreadPoolExecutor` (12). **All `asset_store`
     writes happen on the archive thread** (workers only fetch), so the thread-local
     connection is never shared.
  4. `set_archive_meta(size, mtime)`, `finish()`. On fatal error → `status.fail`.

### Config / Settings
- Add `archive_dir` to `Settings` (env `ARCHIVE_DIR`, default `/archive` in container).
  `archive.db` = `<archive_dir>/archive.db`; bytes under `<archive_dir>/assets/`.

### API (`api.py`)
- Open `AssetStore(settings.archive_dir)` (`create_schema`) in `create_app`; on
  `app.state.asset_store`; `ArchiveStatus` on `app.state.archive_status`.
- `POST /api/archive/start` → starts the pass if idle (`{started}`); `mark_running`
  under a lock to avoid a double-start race.
- `GET /api/archive/status` → `ArchiveStatus.snapshot()`.
- `GET /api/asset/{asset_hash}` → cached bytes (validated hex; `status=ok`;
  `Content-Disposition: inline` + `X-Content-Type-Options: nosniff`); 404 otherwise.
- `message_detail` (HTML body): extract refs → `asset_store.cached_asset_hashes(...)` →
  `rewrite_cached_images` BEFORE `sanitize_html`. Local `/api/asset/...` URLs are
  same-origin so the sanitizer keeps them; uncached remote stay blocked/hidden.

### Frontend (`static/`)
- "Archive remote images" toolbar button → confirm dialog with the privacy note →
  `POST /api/archive/start` → poll `/api/archive/status` (~2s) showing
  `Archiving images… scanned/total · N saved · M skipped · K failed`, settling to a
  summary. Re-opening a message after archiving shows cached images automatically.

### Docker / packaging
- A third mount: host `ARCHIVE_DIR` → `/archive` (read-write). `run.sh` defaults the
  host archive dir to a folder **next to the mbox** (`<mbox_parent>/mbox-viewer-archive`)
  so the durable set co-locates; overridable via `ARCHIVE_DIR`. `Dockerfile` sets
  `ENV ARCHIVE_DIR=/archive`. README + CLAUDE.md document the backup/delete-Gmail story.

## Data flow

Archive: button → `POST /api/archive/start` → daemon `run_archive` → (short-circuit or)
scan messages → skip trackers / download others → `archive.db` + `<archive_dir>/assets/`
→ progress via `GET /api/archive/status`.

View: `GET /api/messages/{id}` → `rewrite_cached_images` (cached remote → `/api/asset/{hash}`)
→ `sanitize_html` → iframe → browser loads cached images from `/api/asset/{hash}`.

## Error handling

- `fetch_image` never raises; failures recorded `status='failed'` with a reason; re-run
  retries `failed`.
- Malformed messages during the scan are skipped (logged), like indexing.
- Double `POST /api/archive/start` while running → `{started:false}`.
- `ok` row but missing byte file → `/api/asset` returns 404 (treated as uncached).
- `create_schema` adds `archive.db` tables on first use; the **index** schema gains
  nothing (assets live in the separate archive DB), so the existing index DB is untouched.

## Testing

- `assets.py`: `url_hash`/`normalize`; `extract_image_refs` (img dims, protocol-relative,
  CSS url(), ignores cid/data); `is_tracking_pixel`; `fetch_image` vs a local stub server
  (success, non-image, oversize, network error → never raises); byte-cache roundtrip;
  `rewrite_cached_images` (cached→local, uncached untouched).
- `assetstore.py`: upsert/get/status/cached/counts; archive_meta get/set.
- `archive.py`: over a small mbox + local stub server — real image stored `ok`, 1×1
  pixel `skipped` and **never requested** (assert the stub never saw it), failed fetch
  recorded, resumable (second run doesn't re-request), and **short-circuit** when
  size/mtime match with no failures (assert the stub gets zero requests).
- API: archive start idempotent; status shape; `/api/asset` serves bytes + nosniff +
  404s; `message_detail` rewrites a cached image to `/api/asset/<hash>`.
- End-to-end: stub image server + mbox, dev server; archive, confirm offline render and
  the short-circuit on re-run; then a live check + redeploy against the real mailbox
  (with the new `/archive` host mount).

## Out of scope (YAGNI)

- Refreshing/expiring already-cached assets.
- Non-image remote resources (CSS/fonts); `cid:`/`data:` handling.
- Per-sender rules; bandwidth throttling beyond the concurrency cap.
- A cancel button for an in-progress pass (restart the process to stop it).
