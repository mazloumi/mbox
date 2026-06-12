# Franklin Extensions from mbox — Design Spec

**Status:** Forward-looking. Approved direction; **build is gated on Franklin framework v1**
(extension host, JMAP-on-Stalwart, admin portal, manifest/scope enforcement, `box://`
viewer). Nothing here ships until that framework exists; these documents exist so the work
is ready to execute the moment it does.

**Source documents:** `franklin-email/project-context.md`, `franklin-email/extension-api-v1.md`
(the authoritative extension API). This spec does not modify them.

**Companion plans:**
- `docs/superpowers/plans/2026-06-12-franklin-foundation.md`
- `docs/superpowers/plans/2026-06-12-franklin-mbox-importer.md`
- `docs/superpowers/plans/2026-06-12-franklin-attachment-library.md`
- `docs/superpowers/plans/2026-06-12-franklin-conversational-ai.md`

---

## 1. Goal

Carry the durable capabilities of the **mbox viewer/assistant** into **Franklin** as a small
set of first-party **extensions**, reusing mbox's proven code where it transfers and rewriting
only the data layer that assumes a static file. mbox stays a standalone product and is left
untouched; this is a *capability transfer*, not a migration.

## 2. What this is (and is not)

Franklin is a personal mail-server platform (Stalwart + a Go relay/tunnel + a control plane +
deliverability ops). mbox is none of that. mbox is the **proof-of-concept for three first-party
extensions** in Franklin's marketplace. The Franklin extension API is explicitly
language-agnostic ("if it runs in a container and speaks HTTP, it's an extension"), so the
extensions stay **Python/FastAPI** — there is no Python→Go rewrite.

**In scope:** three extensions seeded by mbox, plus a shared foundation library they share.
**Out of scope (not from mbox):** the relay, QUIC/TCP tunnel agent, SNI router, encrypted
offline queue, box-side DKIM, control plane, Stripe, deliverability/IP ops, the admin portal
itself, and the extension host. Also out of scope: a full webmail client (compose/send/move) —
standard IMAP/JMAP clients cover daily mail; only the parts clients *can't* do are built here.

## 3. The one fundamental change

Every hard problem reduces to swapping mbox's single dependency:

| | mbox today | Franklin extension |
|---|---|---|
| Mail source | one immutable `.mbox` file, read by byte offset (`reader.py`) | live mailbox over **JMAP** (Stalwart), mutable, continuous |
| Indexing | one-time full scan (`indexer.py`) | initial backfill **+ incremental delta sync** |
| Staleness trigger | file size/mtime | `events:subscribe` SSE → `Email/changes` since a stored JMAP state cursor |
| Mail access | direct file bytes | scoped `JMAP_TOKEN`, declared scopes, internal-only network |

`reader.py` (the mboxrd byte parser) is the only module truly incompatible with live mail —
and it is exactly what the **Importer** needs, so it relocates rather than dies.

## 4. Decomposition into extensions

Three extensions + one shared library. Browsing messages/folders is **dropped** (any IMAP/JMAP
client does it better). Citations resolve in the **portal `box://` viewer** (§12 of the API),
not a per-extension viewer.

| Extension | Purpose | Scopes (broker era) | mbox origin |
|---|---|---|---|
| **mbox/Takeout Importer** | Bulk-import a `.mbox`/Takeout (and IMAP pull) into the live mailbox | `mail:write`, `net:domains:[imap.gmail.com,imap.mail.yahoo.com,outlook.office365.com]`, UI | `reader.py` + `extract.py` verbatim; new = JMAP `Email/import` |
| **Attachment Library** | A unified, mountable view of *all* attachments by type/date/sender — the thing clients can't do | `mail:read`, UI (+ `fs-rw:/path` for export) | `filetypes.py`, `extract.py`, gallery; new = WebDAV server + JMAP-backed tree |
| **Conversational AI** | Cited, grounded chat over the whole mailbox + the attachment-catalog tool | `consumes: embeddings/v1`, `net:lan` (Ollama, default) or `net:domains:[api.anthropic.com]` (opt-in), UI | `chunker`/`embed`/`embed_index`/`retrieve`/`assistant` + catalog tool |

### 4.1 The embedder phasing (per API §12)

The Conversational AI bundles chunking + embedding + retrieval. The API's service broker
(`provides`/`consumes`) is **post-v1**; the manifest fields are reserved and schema-valid in v1.

- **v1.0 interim (no broker):** AI ships as **one container** bundling the index/embedder.
  Its manifest already declares `provides: embeddings/v1, search-index/v1` and
  `consumes: embeddings/v1` so the later split is a packaging change, not a rewrite. It holds
  `mail:read` itself in this form.
- **v1.1 target (broker live):** split into an **Index/Embedder provider** (`mail:read`,
  `events:subscribe`, **no net**, `provides: embeddings/v1` + `search-index/v1`, owns the
  vector volume) and a **Conversational AI consumer** (`consumes: embeddings/v1`, net for the
  LLM, UI, **no `mail:read`** — it gets snippets through the broker). The Attachment Library can
  later also consume `search-index/v1` for semantic attachment search.

This isolates all mail access to one auditable extension and makes the transitive-disclosure
rule load-bearing (mail → Embedder → AI → LLM provider).

## 5. Shared foundation (`franklin-mbox-common`)

A small Python package, **vendored into each extension image at build time** (build-time sharing,
not a runtime service — runtime cross-extension calls are broker-only and post-v1). Contents:

1. **Pure libraries lifted from mbox, unchanged:** `extract` (attachment bytes → text:
   pdf/docx/doc/pptx/xls/ics/vcard/tnef/zip listing), `sanitize` (email HTML → safe HTML),
   `filetypes` (mime/ext → category + `fine_type`), `chunker` (text → windowed chunks).
2. **JMAP client** (`httpx`-based; `httpx` is already an mbox dep): typed wrappers over
   `Mailbox/get`, `Email/query` (paged), `Email/get`, `Email/changes` (delta sync),
   blob download (attachment bytes), `Email/set`/`Email/import` (write, for the Importer).
   Reads `JMAP_URL` + `JMAP_TOKEN` from the runtime env (§5 of the API).
3. **Extension scaffold:** a FastAPI base that wires the Franklin runtime contract — `/healthz`,
   listen on `EXT_HTTP_PORT`, read `EXT_CONFIG_PATH`/`EXT_DATA_DIR`, verify portal identity
   headers (`X-Box-User`, `X-Box-Ext-Session` against the box JWKS), and serve UI under a
   `/ext/<id>/`-rebased base path. No extension implements its own login.
4. **Incremental sync engine:** initial backfill (`Email/query` paging → `Email/get` → store) +
   delta loop (`events:subscribe` SSE wakes → `Email/changes(sinceState)` → apply
   adds/updates/removes), persisting the JMAP state cursor in the extension's store. Replaces
   mbox's size/mtime `index_is_current` check. Handles deletes and mailbox moves that a static
   file never had.

mbox's `store.py` (SQLite + FTS5 + `sqlite-vec`) schema transfers; it gains a sync-cursor row
and must apply deletes/moves rather than only inserts.

## 6. Per-extension design

### 6.1 mbox/Takeout Importer

A one-shot job with a small UI. Flow: user uploads a `.mbox`/Takeout (or supplies IMAP host +
app password) → the extension parses it with `reader.iter_message_spans`/`read_message`
(verbatim mbox code) → for each message, builds the RFC-5322 bytes → writes to the mailbox via
JMAP `Email/import` (with `X-Gmail-Labels` mapped to JMAP mailboxes/keywords) → reports
progress + a per-message skip log (mbox's all-or-nothing-per-message discipline). Idempotent on
re-run (dedupe by `Message-ID`). Matches API §10's "Liberation — Import mode only".

- **Scopes:** `mail:write` (implies read), `net:domains:[...]` (IMAP pull only), UI.
- **Credentials:** app passwords held per the API's connector-credential standard (§13 open
  item — leaning a box secrets vault); until then, encrypted at rest in `EXT_DATA_DIR`.
- **Transfers:** `reader.py`, `extract.py` (for an import preview/verification), the label
  parsing. **New:** JMAP write path, IMAP puller, upload UI.

### 6.2 Attachment Library

The complementary "files" feature, reframed away from in-browser message browsing.

- **WebDAV mount (primary):** a read-only WebDAV server (e.g. `wsgidav`) exposing attachments as
  a virtual filesystem — folders by category/date/sender — backed by JMAP blob downloads; no
  second copy of the data. Mounted in Finder/Explorer, or browsed in the portal-proxied UI, or
  remotely via the SNI route. Tree metadata comes from a light JMAP-driven attachment index (no
  embeddings).
- **fs-export (optional):** periodically writes real files to a host path (`fs-rw:/path`) — the
  API's "attachment auto-extract to local folders".
- **Browser gallery (optional):** mbox's thumbnail gallery + inline viewers, under `/ext/<id>/`.
- **Scopes:** `mail:read`, UI, optional `fs-rw:/path`; later `consumes: search-index/v1`.
- **Transfers:** `filetypes.py`, `extract.py`, gallery/viewer UI. **New:** WebDAV layer,
  attachment-tree index, fs-export job.

### 6.3 Conversational AI

mbox's crown jewel, nearly intact.

- **Pipeline (transfers):** `chunker` → `embed` (local fastembed default) → `embed_index`
  (now incremental, driven by the sync engine) → `retrieve` (hybrid vector + FTS, RRF) →
  `assistant` (cited streaming answers, multi-turn, the `query_attachments` catalog tool).
- **Citations:** emit `box://message/<jmapId>` (and `box://blob/<id>` for attachments) instead
  of mbox's in-app reader links; the portal viewer resolves them. Drops mbox's citation-pane
  code; keeps the chip-rendering + streaming-clickable behavior.
- **Generation backend:** **Ollama/LAN by default** (privacy story — API §5 example is
  "local Ollama, network: LAN-only"); Anthropic as opt-in that surfaces the
  `net:domains:[api.anthropic.com]` egress at full warning weight. mbox already abstracts the
  embed backend (local/ollama); add the same abstraction for *generation* (currently
  Anthropic-only in `assistant.make_anthropic_generate`).
- **Index build cost:** identical one-time backfill embed (mbox measured ~3.1 h CPU /
  ~1.9 h Ollama-GPU for 54k msgs / 108k chunks), then cheap incremental embedding as mail
  arrives. The build runs in the extension; progress surfaced in its UI.
- **Scopes:** v1.0 bundled — `mail:read`, `events:subscribe`, net for LLM, UI,
  `provides`/`consumes` declared but inert. v1.1 — consumer-only as in §4.1.

## 7. Capability transfer summary

| mbox module | Fate | Lands in |
|---|---|---|
| `extract.py`, `sanitize.py`, `filetypes.py`, `chunker.py` | **As-is** | `franklin-mbox-common` |
| `store.py` (SQLite/FTS5/sqlite-vec) | **Transfers**; add sync cursor + apply deletes/moves | foundation + AI/Library |
| `chunker`/`embed`/`embed_index`/`retrieve`/`assistant` | **~As-is**; incremental source | Conversational AI |
| `reader.py` (mboxrd parser) | **Relocates** | Importer |
| `indexer.py` (one-time scan) | **Rewritten** as JMAP backfill + delta | foundation sync engine |
| `assets`/`assetstore`/`archive` (tracker/remote-image) | **Splits**: view-time blocking → Library viewer; on-receive stripping → a separate Tracker-Stripper hook ext (not in this spec; API §11 already covers it) | — |
| `api.py` + `static/` (UI) | **Transfers**; portal-mounted, header-auth, base-path rebased | Library + AI |
| message/folder browsing UI | **Dropped** (clients do it) | — |
| `config`/`status`/`main` | Adapt to extension runtime | each ext |

## 8. Security & scope mapping

- **Default deny**, scopes declared in the manifest and shown at install (API §2–§4).
- **Citations are names, not capabilities** (API §12): emitting `box://message/X` grants nothing;
  the portal resolves it under the *user's* session.
- **Transitive disclosure** (API §12): in the broker era, the AI consuming the Index/Embedder
  (which holds `mail:read`) must disclose that on its install screen; the LLM egress
  (`net:domains:[api.anthropic.com]`) carries full warning weight, which is why Ollama/LAN is the
  default.
- **No extension touches** box identity keys, tunnel/TLS credentials, or other extensions'
  volumes (API §4) — none of these extensions need to.
- **Importer credentials** (IMAP app passwords) follow the API's connector-credential standard
  once decided (§13 open item); interim: encrypted in `EXT_DATA_DIR`.

## 9. Framework dependencies (build is blocked until these exist)

1. Extension host: container lifecycle, manifest validation, scope enforcement, config delivery.
2. JMAP on Stalwart with scoped per-extension tokens (`JMAP_URL`/`JMAP_TOKEN`), incl.
   `Email/import` for the Importer.
3. Admin portal: reverse-proxy at `/ext/<id>/`, identity headers + JWKS, the `box://` viewer.
4. `events:subscribe` SSE stream (API §8).
5. The exact JMAP capability subset exposed in v1 (API §13 open item #1) — affects the JMAP
   client surface.
6. For the v1.1 split only: the service broker + `BOX_SERVICES_URL` (post-v1).
7. Connector-credential standard (API §13 open item #7) — affects the Importer's IMAP path.

Each plan lists which of its tasks are blocked on which dependency, and which can be built/tested
now against a **local Stalwart + a mock-box harness** (see §10).

## 10. Build approach & phasing

Order follows dependency and value:

1. **Foundation** (`franklin-mbox-common`): pure libs (buildable now from mbox), JMAP client +
   sync engine + extension scaffold (need a local Stalwart; portal-auth verified against a mock
   box until the real portal exists). Everything else depends on this.
2. **Importer** (smallest, highest strategic value, exercises the JMAP *write* path + packaging).
   Matches API §10 v1.0.
3. **Conversational AI** (the centerpiece), bundled form first.
4. **Attachment Library** (WebDAV + gallery).
5. **v1.1:** split the AI into Index/Embedder provider + AI consumer when the broker ships.

Dev harness: a `docker compose` with a local Stalwart, a seeded test mailbox, and a tiny
**mock box** standing in for the portal (issues JMAP tokens, serves JWKS, reverse-proxies
`/ext/<id>/`, replays `events`) so extensions can be built and tested before the real framework
exists. The mock box is throwaway scaffolding, not a Franklin component.

## 11. Risks & open questions

- **JMAP v1 surface unknown** (API §13 #1) — the client may need a curated subset; design the
  client behind a thin interface so the surface can change.
- **Sync correctness** — deletes/moves/flag changes are new vs. a static file; the delta loop
  needs careful idempotency and a periodic full-reconcile fallback.
- **Blob-fetch performance** — per-attachment JMAP downloads vs. mbox's local byte seeks; needs
  caching in `EXT_DATA_DIR` and lazy fetch (the Library especially).
- **Embedding build time on a Pi/NAS** — mbox's ~3 h CPU build is fine on a laptop but heavy on
  low-power boxes; surface progress, allow Ollama offload, consider deferring/throttling.
- **Generation backend** — adding an Ollama generation path to `assistant` is new code (mbox is
  Anthropic-only today) and is required for the LAN-only privacy default.
- **Mock-box fidelity** — tests passing against the mock must be re-validated against the real
  framework; treat the mock as a stand-in, not a contract.

## 12. Out of scope

The relay/tunnel/SNI/queue/DKIM/control-plane/portal/host (all Franklin core, all Go), a full
compose/send webmail client, the Tracker-Stripper and Backup extensions (already specified in the
API doc), and Contacts/Calendar.
