# Franklin Extension: Attachment Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **GATED on Franklin framework v1** and the **Foundation plan**. **[BLOCKED:n]** = spec §9 dep *n*.

**Goal:** A first-party extension giving a unified, mountable view of *every* attachment in the
mailbox — by category/date/sender — the one "files" capability a normal mail client cannot
provide. Replaces mbox's Files tab as a *complementary* tool (message/folder browsing is dropped;
clients own that).

**Architecture:** A light JMAP-driven attachment index (metadata only — no embeddings) feeds three
surfaces: a read-only **WebDAV** virtual filesystem (primary), an optional **fs-export** job, and
an optional **browser gallery**. Attachment bytes are fetched lazily via JMAP blob download and
cached in `EXT_DATA_DIR`.

**Tech Stack:** Python 3.12, FastAPI (scaffold), `wsgidav` (or an ASGI WebDAV impl),
`franklin-mbox-common` (`filetypes`, `extract`, `jmap`, `sync`, `scaffold`).

**Spec:** §6.2 of `docs/superpowers/specs/2026-06-12-franklin-mbox-extensions-design.md`

**Manifest scopes:** `mail:read`, `events:subscribe`, UI; optional `fs-rw:/<export-path>`; later `consumes: search-index/v1`.

---

## Prerequisites

- Foundation complete (`jmap`, `sync`, `filetypes`, `extract`, `scaffold`).
- **[BLOCKED:2]** JMAP blob download on dev Stalwart.
- **[BLOCKED:3]** Portal reverse-proxy + (for mounting remotely) the SNI route.

## File Structure

| File | Created/Modified | Responsibility |
|---|---|---|
| `extension.yaml` | Create | manifest |
| `library/index.py` | Create | attachment metadata index (category/date/sender tree) built from JMAP |
| `library/blobcache.py` | Create | lazy JMAP blob fetch + on-disk cache in `EXT_DATA_DIR` |
| `library/webdav.py` | Create | read-only WebDAV exposing the tree |
| `library/export.py` | Create | optional periodic fs-export |
| `library/gallery/` | Create | optional browser gallery (lift mbox Files UI, minus message nav) |
| `tests/` | Create | per-module tests |

---

## Task 1 — Manifest + attachment index [BLOCKED:2]

**Files:** `extension.yaml`, `library/index.py`, `tests/test_index.py`.

- [ ] Manifest: `id: email.franklin.attachment-library`, scopes above, `ui`, `config`
  (`tree_layout: enum[type/date/sender]`, `export_enabled`, `export_path`).
- [ ] **Test (dev Stalwart):** the index enumerates all attachments across the mailbox via JMAP
  (`Email/query`/`get` attachment metadata: name, mime, size, blobId, message jmapId, date,
  sender), grouped by `filetypes.category_for`/`fine_type`; counts match the seeded mailbox; a
  delta (new mail with an attachment) is picked up by the sync engine.
- [ ] Implement the index over `franklin_mbox_common.sync` + `filetypes` (reuse mbox's category
  logic). Store metadata in `extstore`; no embeddings.
- [ ] Run tests. Commit.

## Task 2 — Blob cache [BLOCKED:2]

**Files:** `library/blobcache.py`, `tests/test_blobcache.py`.

- [ ] **Test:** first access downloads via JMAP and caches in `EXT_DATA_DIR`; second access serves
  from cache; cache respects a size budget (LRU eviction); a missing/expired blobId re-fetches.
- [ ] Implement lazy fetch + LRU disk cache keyed by `blobId`. Commit.

## Task 3 — WebDAV surface [BLOCKED:2,3]

**Files:** `library/webdav.py`, `tests/test_webdav.py`.

- [ ] **Test:** a WebDAV client (e.g. `webdavclient3`) lists the virtual tree
  (`/<category>/<sender-or-date>/<filename>`), `GET`s a file and receives the correct bytes
  (via blob cache), and the FS is **read-only** (PUT/DELETE rejected). Mount path is served under
  `/ext/<id>/dav/` (portal-proxied) and is portal-authenticated.
- [ ] Implement a read-only WebDAV provider backed by `index` (tree) + `blobcache` (bytes).
  Deduplicate identical blobs; make filenames collision-safe.
- [ ] Run tests. Commit. *(Remote mounting via the SNI route is config-only once the portal exposes
  it; no extension change.)*

## Task 4 — fs-export (optional) [BLOCKED:3 for fs-rw grant]

**Files:** `library/export.py`, `tests/test_export.py`.

- [ ] **Test:** with `export_enabled` + an `fs-rw:/path` grant, the job writes every attachment to
  `<path>/<category>/...`, is incremental (only new/changed), and never writes outside the granted
  path.
- [ ] Implement the periodic export job over `index` + `blobcache`. Commit.

## Task 5 — Browser gallery (optional) [BLOCKED:3]

**Files:** `library/gallery/`, routes in the scaffold app.

- [ ] **Test (mock box):** the gallery lists categories with counts, shows an image thumbnail grid
  and inline viewers (PDF/image/audio/video/table), and deep-links each item's source message as
  `box://message/<jmapId>` (resolved by the portal viewer). No message-folder navigation.
- [ ] Lift mbox's Files-tab gallery + viewers (`app.js`/`style.css` subset), strip the
  folders/message-list code, rebase under `/ext/<id>/`, and replace mbox's in-app message links
  with `box://` links.
- [ ] Run tests. Commit.

## Task 6 — Packaging + review

- [ ] `Dockerfile`; image builds; mounts cleanly in Finder/Explorer against the dev box.
- [ ] End-to-end: install → mount the WebDAV drive → browse all PDFs/photos → open one → confirm
  bytes match; toggle fs-export and verify files appear; open the gallery and click a `box://`
  citation through to the portal viewer.
- [ ] Final review against spec §6.2. Commit.

---

## Done when

A user can mount their mailbox's attachments as a read-only drive (and/or export them to a folder,
and/or browse them in a gallery), with content fetched lazily and cached, and every item linking
back to its source email via `box://`.
