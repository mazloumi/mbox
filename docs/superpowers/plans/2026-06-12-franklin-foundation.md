# Franklin Foundation (`franklin-mbox-common`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **GATED:** This plan is **blocked on Franklin framework v1** for the runtime-contract and JMAP tasks (see Prerequisites). Tasks marked **[NOW]** can be built and tested today against mbox + a local Stalwart + the mock-box harness. Tasks marked **[BLOCKED:n]** need framework dependency *n* from the spec §9.

**Goal:** A reusable Python package shared (at build time) by all mbox-derived Franklin
extensions: the pure mbox libraries, a JMAP client, an incremental sync engine, and a FastAPI
extension scaffold implementing the Franklin runtime contract.

**Architecture:** `franklin-mbox-common` is an installable package (`pip install -e` in dev,
vendored/baked into each extension image in prod). It owns nothing user-facing; it is the
substrate. The pure libs are lifted from mbox unchanged; the JMAP client + sync engine replace
mbox's `reader.py`/`indexer.py`; the scaffold wires `/healthz`, `EXT_*` env, and portal auth.

**Tech Stack:** Python 3.12, FastAPI, `httpx`, SQLite (`sqlite-vec` for consumers), `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-12-franklin-mbox-extensions-design.md`

---

## Prerequisites

- **[NOW]** Source mbox checkout (for lifting `extract`/`sanitize`/`filetypes`/`chunker`/`store`).
- **[BLOCKED:2]** Local Stalwart with JMAP enabled + a seeded test mailbox (dev compose).
- **[BLOCKED:1,3,4]** Mock-box harness: issues a `JMAP_TOKEN`, serves `/.well-known/box-jwks`,
  signs `X-Box-Ext-Session`, reverse-proxies `/ext/<id>/`, replays `events` SSE. Throwaway
  scaffolding standing in for the portal/host until the real framework exists.

## File Structure

| File | Created/Modified | Responsibility |
|---|---|---|
| `franklin_mbox_common/extract.py` | Lift from mbox | attachment bytes → text |
| `franklin_mbox_common/sanitize.py` | Lift from mbox | email HTML → safe HTML |
| `franklin_mbox_common/filetypes.py` | Lift from mbox | mime/ext → category + `fine_type` |
| `franklin_mbox_common/chunker.py` | Lift from mbox | text → windowed chunks |
| `franklin_mbox_common/mboxrd.py` | Lift from mbox `reader.py` | mboxrd parse (Importer only) |
| `franklin_mbox_common/jmap.py` | Create | JMAP client (query/get/changes/blob/import/set) |
| `franklin_mbox_common/sync.py` | Create | backfill + delta sync engine |
| `franklin_mbox_common/extstore.py` | Adapt from mbox `store.py` | SQLite schema + sync cursor + apply deletes/moves |
| `franklin_mbox_common/scaffold.py` | Create | FastAPI base: `/healthz`, `EXT_*`, portal auth, base-path |
| `tests/` | Create | one `test_*.py` per module (TDD, real artifacts) |
| `dev/compose.yaml`, `dev/mockbox/` | Create | local Stalwart + mock-box harness |

---

## Task 1 — Lift the pure libraries [NOW]

**Files:** Create `franklin_mbox_common/{extract,sanitize,filetypes,chunker,mboxrd}.py`; copy
`tests/test_{extract,sanitize,filetypes,chunker}.py` from mbox.

- [ ] Copy the four pure modules + `reader.py`→`mboxrd.py` from mbox unchanged (Python 3.12, so
  drop the 3.9 `Optional` constraint is allowed but not required).
- [ ] Copy mbox's matching tests + the `sample_mbox`/reportlab/python-docx fixtures.
- [ ] Run `pytest` — expected: all lifted tests pass unchanged. This proves the lift is clean.
- [ ] Commit.

## Task 2 — JMAP client [BLOCKED:2,5]

**Files:** Create `franklin_mbox_common/jmap.py`, `tests/test_jmap.py`.

A thin, typed `httpx` wrapper. Keep the surface behind an interface so the v1 JMAP subset (spec
risk §11) can change without touching callers.

- [ ] **Test first:** against the dev Stalwart, assert `JmapClient(session).mailboxes()` returns
  the seeded mailboxes; `query_emails(limit=10)` returns ids; `get_emails([id])` returns
  metadata matching what was seeded; `download_blob(blobId)` returns the attachment bytes.
- [ ] Implement session bootstrap (`GET JMAP_URL/.well-known/jmap` → account id, capabilities),
  then methods: `mailboxes()`, `query_emails(filter, position, limit) -> (ids, state)`,
  `get_emails(ids, properties)`, `changes(sinceState) -> {created, updated, destroyed, newState}`,
  `download_blob(blobId) -> bytes`. Auth: `Authorization: Bearer $JMAP_TOKEN`.
- [ ] Add `import_email(raw_bytes, mailboxIds, keywords)` (`Email/import`) and
  `set_email(id, patch)` (`Email/set`) — used by the Importer; covered by its own plan's tests.
- [ ] Run tests against dev Stalwart. Commit.

## Task 3 — Extension store (schema + deltas) [NOW for schema, BLOCKED:2 for sync apply]

**Files:** Adapt `franklin_mbox_common/extstore.py` from mbox `store.py`; `tests/test_extstore.py`.

- [ ] Lift mbox `store.py` schema (messages, labels, attachments, FTS5, chunks, `vec_chunks`,
  `meta`). Add a `sync_state` row in `meta` (JMAP state cursor) and a stable `jmap_id` column on
  `messages` (replacing mbox's `offset`/`length`).
- [ ] **Test:** insert, then `apply_changes({created:[...], updated:[...], destroyed:[id]})`
  removes the destroyed message + its FTS/vector/chunk rows and re-indexes updated ones. mbox
  only ever inserted; deletes/moves are new — test them explicitly.
- [ ] Implement `apply_changes` (idempotent), `get_sync_state`/`set_sync_state`. Reuse mbox's
  FTS/vector write paths.
- [ ] Run tests. Commit.

## Task 4 — Incremental sync engine [BLOCKED:2,4]

**Files:** Create `franklin_mbox_common/sync.py`, `tests/test_sync.py`.

- [ ] **Test:** seed Stalwart with N messages → `backfill()` indexes all N and records a state
  cursor. Add 1 / delete 1 in Stalwart → `poll_delta()` applies exactly that change. Restart
  mid-backfill → resumes (cursor/paging), no dupes.
- [ ] Implement `backfill(jmap, store, embed_hook=None)`: page `query_emails` → `get_emails` →
  `store.apply_changes(created=...)`; commit every N; record state. (Mirrors mbox `indexer`,
  source = JMAP.)
- [ ] Implement `poll_delta(jmap, store)`: `changes(store.get_sync_state())` →
  `store.apply_changes(...)` → `set_sync_state(newState)`.
- [ ] Implement `run(events_url)`: subscribe to `events` SSE; on `message.*` wake `poll_delta`;
  periodic full-reconcile fallback (spec risk §11). `embed_hook` lets consumers vectorize new
  chunks incrementally.
- [ ] Run tests. Commit.

## Task 5 — Extension scaffold (Franklin runtime contract) [BLOCKED:1,3]

**Files:** Create `franklin_mbox_common/scaffold.py`, `tests/test_scaffold.py`.

- [ ] **Test (against mock box):** an app built with the scaffold answers `GET /healthz` 200;
  rejects requests lacking a valid `X-Box-Ext-Session` (verified against mock JWKS); exposes the
  parsed `X-Box-User`; serves a UI asset correctly under the `/ext/<id>/` base path; reads
  `EXT_CONFIG_PATH`/`EXT_DATA_DIR`/`EXT_HTTP_PORT`.
- [ ] Implement `build_app(ext_id) -> FastAPI`: `/healthz`; middleware verifying portal identity
  headers against `/.well-known/box-jwks`; config loader (`EXT_CONFIG_PATH` JSON,
  reload on `SIGHUP`/restart per manifest); static mount + base-path rewriting; data dir helper.
- [ ] Run tests against the mock box. Commit.

## Task 6 — Dev harness [BLOCKED:2; mock box is [NOW]-buildable]

**Files:** Create `dev/compose.yaml`, `dev/mockbox/` (small FastAPI app), `dev/seed_mailbox.py`.

- [ ] Compose: Stalwart + the mock box + a seed step that loads a known test mailbox (reuse
  mbox's `sample_mbox` content via the Importer once it exists, or IMAP-append directly).
- [ ] Mock box: issue a JMAP token, serve JWKS, sign sessions, reverse-proxy `/ext/<id>/`,
  replay `events` SSE on mailbox change. Document it as throwaway.
- [ ] Commit.

## Task 7 — Package + CI [NOW]

- [ ] `pyproject.toml` for `franklin-mbox-common`; `pip install -e .` works; `pytest` green.
- [ ] CI runs the [NOW] tests always and the [BLOCKED] tests when the dev compose is up.
- [ ] Final review: confirm the pure libs are byte-identical to mbox (no accidental drift) and the
  JMAP/sync/scaffold interfaces are minimal and stable. Commit.

---

## Done when

`franklin-mbox-common` installs, all [NOW] tests pass standalone, and all [BLOCKED] tests pass
against the dev Stalwart + mock box. The three extension plans build on this package.
