# Archive Retry Cap (permanently-unreachable images) — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** the remote-image-archiving feature.

## Goal

Stop persistently-dead image hosts from being re-fetched on every archive run and from
holding the footer at "click to update" forever. After a capped number of failed
attempts an image is marked **permanently unreachable** (terminal): it is no longer
retried, no longer blocks the short-circuit, and the footer shows ✓ with an
`unreachable` count.

## Problem today

`failed` assets are retried on every run (the scan re-includes them), and both the
unchanged-mbox short-circuit and `up_to_date` require `failed == 0`. So N permanently
dead hosts cause a full re-scan + re-fetch every run and keep `up_to_date` false. (Live
example: 9,777 failed images out of 49,584.)

## Design

### Data model (`assetstore.py`)
- `assets` gains `attempts INTEGER NOT NULL DEFAULT 0`. Added to `ASSET_SCHEMA`'s
  `CREATE TABLE` (fresh DBs) **and** applied to existing DBs via a guarded migration in
  `create_schema`:
  ```python
  try:
      self.conn.execute("ALTER TABLE assets ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
  except sqlite3.OperationalError:
      pass  # column already exists
  ```
  Existing rows keep their data and start at `attempts = 0`.
- New terminal status value `gave_up` (alongside `ok` / `skipped` / `failed`).
- `upsert_asset(...)` gains a trailing `attempts=0` parameter (stored in the new column).
- New `get_attempts(url_hash) -> int` (0 when absent).
- `asset_counts()` returns an additional `gave_up` key:
  `{ok, skipped, failed, gave_up, total}`.

### Worker (`archive.py`)
- `MAX_FETCH_ATTEMPTS = 3`.
- Scan: the "already terminal, skip" check becomes `asset_status(h) in ("ok", "skipped",
  "gave_up")` — `gave_up` is terminal and is never re-fetched. `failed` is still retried.
- Download phase, on a failed fetch:
  - `attempts = asset_store.get_attempts(h) + 1`.
  - `status = "gave_up" if attempts >= MAX_FETCH_ATTEMPTS else "failed"`.
  - `upsert_asset(h, url, None, None, w, ht, status, res.error, _now(), attempts=attempts)`.
- `ok` and `skipped` upserts pass `attempts=0`.
- The live `ArchiveStatus` is unchanged: a give-up still calls `inc_failed()` for that
  run's progress readout (it failed to download this run). The authoritative
  failed-vs-unreachable split is the persisted `asset_counts()` shown when idle.

### Short-circuit & up_to_date — unchanged condition, new effect
- `run_archive` short-circuit and `api` `up_to_date` both keep the `counts["failed"] == 0`
  condition. Because `gave_up` is a separate bucket, exhausted images no longer count as
  `failed`, so once all dead hosts are given up the run short-circuits and the footer
  shows ✓ — even with `gave_up > 0`.

### API
- No route changes. `/api/archive/status.archived` already returns `asset_counts()`,
  which now includes `gave_up`. `up_to_date` logic is unchanged (it already keys off
  `failed`).

### Frontend (`static/app.js`)
- The idle footer breakdown gains an `unreachable` segment:
  `Images: {total} total · {ok} archived · {skipped} skipped · {failed} failed · {gave_up} unreachable`,
  suffixed ` ✓` when `up_to_date`, else ` · click to update`.
- The running readout is unchanged (`… {downloaded} saved · {skipped} skipped ·
  {failed} failed`).

## Migration / behavior for the current archive
The existing 9,777 `failed` rows have `attempts = 0` after the migration. Each
"Archive remote images" click retries them (`failed`, attempts 1 → 2) and on the third
attempt they become `gave_up`; the run after that skips them entirely and short-circuits,
flipping the footer to ✓ with `9,777 unreachable`. Any host that recovers in the meantime
is archived (`ok`) instead.

## Testing
- `assetstore`: a DB created without `attempts` (simulated by creating the table without
  the column, then `create_schema`) gains the column with default 0 and no data loss;
  `upsert_asset` persists `attempts`; `get_attempts` returns it (0 when absent);
  `asset_counts` includes `gave_up`.
- `archive`: a URL served by a stub that always 404s/non-image becomes `failed`
  (attempts 1, then 2) over two runs, then `gave_up` on the third; a fourth run does
  **not** request it again (assert the stub recorded no further hits) and short-circuits
  (`status.snapshot()` reflects no new work, `asset_counts()["failed"] == 0`).
- `api`/frontend: `/api/archive/status.archived` includes `gave_up`; footer renders the
  `unreachable` segment and ✓ once `failed == 0`; verified live in the browser.

## Out of scope (YAGNI)
- A manual "retry unreachable" / reset button (re-archiving handles recoveries for
  `failed`; a `gave_up` reset is a future nicety).
- Per-host backoff/scheduling; distinguishing 404 (permanent) from timeout (transient)
  — the attempt cap covers both.
