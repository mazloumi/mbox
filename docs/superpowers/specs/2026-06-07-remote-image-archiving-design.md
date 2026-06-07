# Remote Image Archiving (offline image cache) — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** the mbox viewer + background-indexing/inline-PDF work.

## Goal

Let the user **archive remote images** referenced by emails so they display offline,
independent of the original hosts being available or the user being online. Archiving
is **opt-in** (a UI button), **skips tracking pixels** (never fetched, so no signal is
sent), and once done, cached images **display automatically** with no network call and
no tracker ping at view time.

## Why opt-in / privacy

Fetching a remote image signals the sender that the email was opened (IP + timestamp).
Bulk-archiving would do that across the whole archive, so it must be a conscious,
explicit action. Mitigations: opt-in only; skip likely tracking pixels before any
fetch; honor `HTTP_PROXY`/`HTTPS_PROXY` env so the user can route egress through a
VPN/proxy. Viewing cached images afterward is private (served locally).

## Approach

Approach **A** (chosen): a self-contained background archive pass over the **existing**
index — walks all messages by stored byte-offsets, extracts remote image URLs,
dedupes, skips trackers, downloads the rest, caches to disk. No indexer changes; no
re-index required. Rejected: (B) recording URLs during indexing (would force a
re-index of the 13GB file); (C) cache-on-view only (incomplete offline archive).

## Components

### Storage
- New table:
  `assets(url_hash TEXT PRIMARY KEY, url TEXT, content_type TEXT, size INTEGER,
  width INTEGER, height INTEGER, status TEXT, error TEXT, fetched_at TEXT)`,
  `status ∈ {ok, skipped, failed}`.
- Bytes stored on disk at `<index_dir>/assets/<url_hash>` (keeps the SQLite file lean;
  lives in the persistent `/index` volume). `url_hash = sha256(url) hex`.
- Keyed by URL hash → automatic dedup (a logo shared by thousands of emails is fetched
  once). Resumable: a re-run skips rows already `ok`/`skipped`, retries `failed`.
- `Store` gains: `upsert_asset(...)`, `get_asset(url_hash)`, `asset_status(url_hash)`,
  `cached_asset_hashes(url_hashes)` (bulk "which of these are ok"), `asset_counts()`.

### Module `assets.py` (pure, no SQLite)
- `url_hash(url) -> str` — sha256 hex.
- `extract_image_refs(html) -> list[(url, width, height)]` — parse `<img>` tags
  (capturing `src`, `width`, `height`) plus CSS `url(...)` in inline `style`/`background`.
  Returns only remote refs (`http://`, `https://`, protocol-relative `//host`);
  resolves protocol-relative to `https:`.
- `is_tracking_pixel(url, width, height) -> bool` — true if declared `width <= 2` or
  `height <= 2`, or the URL host matches a small built-in tracker-domain blocklist
  (e.g. common open-tracking domains). Heuristic; not perfect (accepted).
- `fetch_image(url) -> FetchResult` — HTTP GET with a 10s timeout, honoring proxy env;
  returns bytes + content-type on success. Rejects (returns a failure result, never
  raises) when: non-2xx, content-type not `image/*`, body exceeds `MAX_ASSET_BYTES`
  (10 MB), or any network error. Streams with the size cap enforced mid-read.

### Archive worker (`archive.py`)
- `ArchiveStatus` — thread-safe progress holder (mirrors `IndexStatus`):
  `{running, messages_scanned, total_messages, urls_seen, downloaded, skipped, failed,
  error}`.
- `run_archive(settings, store, status)` — the pass:
  1. `total_messages = store.message_count()`; iterate every message row.
  2. For each: read the message (existing `reader.read_message` by offset), get the
     HTML body (`get_display_body`), `extract_image_refs`.
  3. For each unique `url_hash` not already terminal in `assets`:
     - if `is_tracking_pixel` → record `status='skipped'` (never fetched).
     - else enqueue for download.
  4. A bounded `ThreadPoolExecutor` (max 12 workers) runs `fetch_image`; on success
     write bytes to disk + `upsert_asset(status='ok', ...)`; on failure
     `upsert_asset(status='failed', error=...)`.
  5. Update `status` as it goes; set `running=False` at the end; on fatal error set
     `status.error`.
- Started on a daemon thread by the API; only one run at a time (guarded).

### Rendering (`api._render_body` path)
- New `assets.rewrite_cached_images(html, is_cached) -> html` where `is_cached(url)`
  returns True if that URL's asset is `ok`. It replaces remote `<img src>` (and CSS
  `url()`) whose asset is cached with the local `/api/asset/<url_hash>`; leaves
  uncached refs untouched.
- `message_detail` runs `rewrite_cached_images` BEFORE `sanitize_html`. Local
  `/api/asset/...` URLs are same-origin (not remote), so the sanitizer keeps them;
  remaining remote (uncached) images are blanked as today and hidden by the frontend.
  Net effect: cached images display automatically and offline; nothing else changes.
- `is_cached` is backed by a single bulk query (`cached_asset_hashes`) over the URLs in
  that one message — not per-image queries.

### API (`api.py`)
- `POST /api/archive/start` → starts the pass if idle; returns `{started: bool}`
  (`false` if already running).
- `GET /api/archive/status` → `ArchiveStatus.snapshot()`.
- `GET /api/asset/{url_hash}` → cached bytes with the stored `content_type`, headers
  `Content-Disposition: inline` + `X-Content-Type-Options: nosniff`; 404 if not `ok`.
  `url_hash` is validated as hex (defensive; no path traversal — file is read by hash
  from the assets dir).

### Frontend (`static/`)
- An **"Archive remote images"** toolbar button. First click shows a confirm dialog
  with the privacy note ("downloads images from senders' servers; may signal emails
  were opened; tracking pixels are skipped"). On confirm → `POST /api/archive/start`,
  then poll `GET /api/archive/status` (~2s) and show a readout, e.g.
  `Archiving images… 4,120/54,183 · 9,830 saved · 1,240 skipped`. Hide/stop on
  completion. Re-opening a message after archiving shows its cached images
  automatically (the existing render path handles it).

## Data flow

Archive: button → `POST /api/archive/start` → daemon `run_archive` → walk messages →
extract refs → skip trackers / download others → disk + `assets` table → progress via
`GET /api/archive/status`.

View: `GET /api/messages/{id}` → `rewrite_cached_images` (cached remote → `/api/asset/{hash}`)
→ `sanitize_html` → frontend iframe → browser loads cached images from `/api/asset/{hash}`.

## Error handling

- `fetch_image` never raises; all failures recorded as `status='failed'` with a reason.
- Malformed messages during the walk are skipped (logged), like indexing.
- A second `POST /api/archive/start` while running is a no-op (`started:false`).
- Missing/partial asset file on disk for an `ok` row → `/api/asset` returns 404 (treated
  as uncached).
- Proxy/network down → fetches fail and are recorded; the pass completes with failures
  surfaced in the counts; re-run retries `failed`.

## Testing

- `assets.py` units: `url_hash` stable; `extract_image_refs` (img with width/height,
  protocol-relative, CSS url(), ignores `cid:`/`data:`); `is_tracking_pixel`
  (1×1, tiny dims, blocklist host, normal image passes); `fetch_image` against a local
  stub HTTP server (success image/*, non-image rejected, oversize rejected, 404/timeout
  → failure result, never raises).
- `store` units: `upsert_asset`/`get_asset`/`cached_asset_hashes`/`asset_counts`.
- `archive` units: `run_archive` over the `sample_mbox` (extended with a remote `<img>`)
  pointed at a local stub server → asset stored `ok`; a 1×1 pixel recorded `skipped`
  and never requested (assert the stub never received it); `ArchiveStatus` lifecycle.
- `rewrite_cached_images`: cached URL → local `/api/asset/<hash>`; uncached untouched.
- API: `/api/archive/start` idempotent while running; `/api/archive/status` shape;
  `/api/asset/{hash}` serves bytes + nosniff, 404 when absent; `message_detail` shows a
  cached image as a local `/api/asset` URL after archiving.
- End-to-end: small mbox + local image server in a throwaway container; then a live
  check against the real mailbox (archive a handful, confirm a previously-blocked image
  now renders offline).

## Out of scope (YAGNI)

- Re-fetching/refreshing already-cached assets, or expiry.
- Archiving non-image remote resources (CSS, fonts), or `cid:`/`data:` handling.
- Per-sender allow/deny rules; bandwidth throttling beyond the concurrency cap.
- Showing cached images for messages while the archive pass is still mid-run is fine but
  not specially optimized (they appear as their `url_hash` becomes `ok`).
- Cancel button for an in-progress pass (it runs to completion; the process can be
  restarted to stop it).
