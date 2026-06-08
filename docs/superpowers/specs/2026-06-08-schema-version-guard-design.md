# Schema-Version Guard — Design

**Date:** 2026-06-08
**Status:** Approved

## Goal

Force an automatic re-index when the **index format changes** — even if the mbox is unchanged — so a
code deploy that alters the schema or the indexed content (categorization, extraction) can't leave a
stale/incompatible index serving wrong results. Today `index_is_current` only compares the mbox's
size + mtime, so a schema change against an unchanged mbox is silently reused.

## Decision

Add an integer `SCHEMA_VERSION` constant. The indexer stamps it into `meta` on every build;
`index_is_current` returns `False` when the stored version is missing or different from the current
one (in addition to the existing size/mtime check). Bumping `SCHEMA_VERSION` is the one-line switch a
developer flips whenever a change requires a re-index (new column, new/changed extractor,
categorization change). The index is disposable, so the cost is a one-time background re-index.

## Design

### `store.py`
- `SCHEMA_VERSION = 2` — module constant next to `SCHEMA`. (2 = the post-`category`-column format;
  the current production index — built before this constant existed — has no stored version, so it
  reads as stale once and re-indexes on the next start. That one-time re-index is the intended
  migration.)

### `indexer.py`
- `build_index`: after writing `source_size`/`source_mtime`, also
  `store.set_meta("schema_version", str(SCHEMA_VERSION))`.
- `index_is_current`: after the `size is None or mtime is None` guard, also read
  `store.get_meta("schema_version")` and return `False` if it `!= str(SCHEMA_VERSION)`. Keep the whole
  body inside the existing try/except → never raises (the polled `/api/status` depends on it).

No API, schema, or frontend change. `meta(key, value)` already exists.

## Testing
- After `build_index`, `get_meta("schema_version") == str(SCHEMA_VERSION)` and `index_is_current` is
  `True` for the same file.
- With size+mtime matching but the stored `schema_version` set to an old value (e.g. `"1"`),
  `index_is_current` is `False`.
- Missing `schema_version` (an index built before this guard) → `index_is_current` is `False`.
- The existing staleness/missing-mbox tests still pass.

## Out of scope
- Auto-bumping the version from a hash of SCHEMA/extractor code (manual bump is explicit and
  predictable).
- Partial/in-place migrations (the index is disposable; re-index is the migration).
