# Franklin Extension: Conversational AI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **GATED on Franklin framework v1** and the **Foundation plan**. **[BLOCKED:n]** = spec §9 dep *n*.

**Goal:** mbox's crown jewel as a first-party extension: cited, grounded, multi-turn chat over the
whole mailbox, with the attachment-catalog tool, citations as `box://` deep-links, and a
**LAN-only (Ollama) generation default** with Anthropic as an opt-in. Ships v1.0 as a single
bundled container; splits into an Index/Embedder **provider** + AI **consumer** at v1.1 when the
service broker lands.

**Architecture (v1.0 bundled):** the sync engine feeds `chunker → embed → embed_index` (local
fastembed default) into `sqlite-vec`; `retrieve` does hybrid vector+FTS RRF; `assistant` streams
cited answers and runs the `query_attachments` catalog tool. Generation goes to Ollama (LAN) by
default or Anthropic (opt-in egress). Citations are emitted as `box://message/<jmapId>`.

**Architecture (v1.1 split):** the embed/index/retrieve half becomes a **provider**
(`provides: embeddings/v1, search-index/v1`; `mail:read`; **no net**); the chat/UI half becomes a
**consumer** (`consumes: embeddings/v1`; net for the LLM; **no `mail:read`**) calling
`BOX_SERVICES_URL/embeddings/v1/...`.

**Tech Stack:** Python 3.12, FastAPI (scaffold), `franklin-mbox-common`
(`chunker`/`jmap`/`sync`/`extstore`/`scaffold`), `fastembed` (CPU) / Ollama, `anthropic` (opt-in),
vanilla-JS chat UI lifted from mbox.

**Spec:** §6.3 + §4.1 of `docs/superpowers/specs/2026-06-12-franklin-mbox-extensions-design.md`

**Manifest scopes (v1.0):** `mail:read`, `events:subscribe`, `net:lan` (default) or `net:domains:[api.anthropic.com]` (opt-in), UI; `provides`/`consumes` declared but inert.

---

## Prerequisites

- Foundation complete (`chunker`, `sync`, `extstore` with vectors, `scaffold`).
- **[BLOCKED:2,4]** JMAP + `events` on dev Stalwart (for incremental embedding).
- LLM backends in dev: an Ollama host on the LAN; an Anthropic key for the opt-in path.
- **[BLOCKED:6]** Service broker + `BOX_SERVICES_URL` — **only** for Task 7 (the v1.1 split).

## File Structure

| File | Created/Modified | Responsibility |
|---|---|---|
| `extension.yaml` | Create | manifest (v1.0 bundled) |
| `ai/index_build.py` | Adapt mbox `embed_index` | incremental chunk+embed driven by `sync` |
| `ai/retrieve.py` | Lift mbox | hybrid vector+FTS RRF |
| `ai/generate.py` | Create (new abstraction) | generation backend: `ollama` (default) \| `anthropic` |
| `ai/assistant.py` | Adapt mbox | cited streaming answers + `query_attachments` tool; `box://` citations |
| `ai/app.py` | Create | scaffold app + `/api/chat` (NDJSON stream), `/api/capabilities` |
| `ai/static/` | Lift mbox chat UI | chat + streaming-clickable `box://` citations |
| `svc/embeddings.py` | Create (Task 7) | `embeddings/v1` provider endpoint (v1.1) |
| `tests/` | Create | per-module tests |

---

## Task 1 — Manifest + scaffold + capabilities [BLOCKED:1]

- [ ] `extension.yaml`: `id: email.franklin.assistant`, scopes above, `config`
  (`gen_backend: enum[ollama,anthropic]` default `ollama`, `ollama_url`, `gen_model`,
  `embed_model`), `provides: [embeddings/v1, search-index/v1]` + `consumes: [embeddings/v1]`
  declared (inert in v1). **Default `net:lan`**; selecting `anthropic` requires the
  `net:domains:[api.anthropic.com]` scope and re-prompts (transitive/egress disclosure).
- [ ] `ai/app.py` via scaffold; `/api/capabilities` reports build state + active backend.
- [ ] **Test:** manifest validates; `/healthz` + `/api/capabilities` under the mock box. Commit.

## Task 2 — Incremental index build [BLOCKED:2,4]

**Files:** `ai/index_build.py`, `tests/test_index_build.py` (adapt mbox `embed_index`).

- [ ] **Test (dev Stalwart):** backfill embeds all chunks of the seeded mailbox; a newly delivered
  message is chunked+embedded by the delta loop without a full rebuild; a deleted message's
  chunks/vectors are removed (via `extstore.apply_changes`); the re-embed-on-model-change guard
  still fires.
- [ ] Adapt mbox `build_chunks`/`build_embeddings` to be driven by `franklin_mbox_common.sync`
  (source = JMAP, incremental) instead of a one-time mbox scan. Keep the decoupled chunk→embed
  passes, the savepoint discipline, and the embed-meta guard.
- [ ] Run tests. Commit.

## Task 3 — Retrieval [NOW-ish] (depends on Task 2 data)

**Files:** `ai/retrieve.py`, `tests/test_retrieve.py` (lift mbox `retrieve`).

- [ ] **Test:** hybrid vector+FTS RRF returns the expected message ids/snippets for seeded
  queries (reuse mbox's retrieve tests, data from Task 2).
- [ ] Lift mbox `retrieve.py` unchanged (it reads the local index). Commit.

## Task 4 — Generation backend abstraction [NOW]

**Files:** `ai/generate.py`, `tests/test_generate.py`.

mbox is Anthropic-only; the LAN-only privacy default needs a local backend.

- [ ] **Test:** `make_generate(cfg)` returns a streaming `generate(system, messages, tools,
  run_tool)` for both `ollama` (default, LAN) and `anthropic`; both drive the tool-use loop
  (verified with a fake client like mbox's `test_assistant`); `anthropic` path is unreachable
  unless the egress scope/config is set.
- [ ] Implement an Ollama chat backend (tool-use via Ollama's function-calling, streaming) and
  port mbox's `make_anthropic_generate`. Keep mbox's MAX_TOKENS/long-list fix and tool-round
  separator. Commit.

## Task 5 — Assistant + `box://` citations [BLOCKED:2]

**Files:** `ai/assistant.py`, `tests/test_assistant.py` (adapt mbox `assistant`).

- [ ] **Test:** cited answers reference `box://message/<jmapId>`; the `query_attachments` catalog
  tool returns exact counts/lists from the local index (port mbox's catalog tests); the tool-loop
  + streaming behavior matches mbox.
- [ ] Adapt mbox `assistant.py`: system prompt + `ATTACHMENT_TOOL`; change the citation contract
  from mbox's `[#id]` (internal message id) to **`box://message/<jmapId>`** (the JMAP id); keep
  `build_context_block`, `sources_for`, the tool loop, MAX_TOKENS. Commit.

## Task 6 — Chat UI [BLOCKED:3]

**Files:** `ai/static/`, routes in `ai/app.py`.

- [ ] **Test (mock box):** `/api/chat` streams NDJSON (sources → tokens → done); the UI renders
  markdown, citations are clickable **while streaming**, and a citation click navigates to the
  portal `box://` viewer (not an in-app pane). Input disabled until the index is ready.
- [ ] Lift mbox's chat UI (`app.js` chat section + `marked`/`DOMPurify` + the
  streaming-clickable-citation delegation), drop mbox's in-app reader/split-pane, and point
  citations at `box://`. Rebase under `/ext/<id>/`. Commit.

## Task 7 — v1.1 provider/consumer split [BLOCKED:6]

**Files:** `svc/embeddings.py`; second manifest; consumer trimming.

> Do this **only when the service broker ships.** Until then, Tasks 1–6 are the shipping v1.0
> bundle and this task stays unstarted.

- [ ] **Test (broker):** a separate **provider** container exposes `embeddings/v1`
  (`/svc/embeddings/query` → ranked snippets + `box://` refs) over `BOX_SERVICES_URL`; the
  **consumer** (chat/UI) drops `mail:read`, calls the broker, and produces identical answers;
  the consumer's install screen discloses sharing with the provider (transitive rule).
- [ ] Split: move `index_build`/`retrieve` (+ `mail:read`, `events:subscribe`, no net) into the
  provider; the consumer keeps `assistant`/UI/`generate` + net + `consumes: embeddings/v1`.
  Define the `embeddings/v1` interface (decide: returns snippet text vs. refs-only — sets the
  disclosure weight; see spec risk §11). Commit.

## Task 8 — Packaging + review

- [ ] `Dockerfile`(s); image builds; runs on the dev box with Ollama on the LAN.
- [ ] End-to-end: install → watch the index build → ask "how many audio files do I have?"
  (catalog tool, exact count) and a content question (cited answer) → click a `box://` citation
  through to the portal viewer → switch backend to Anthropic and confirm the egress disclosure.
- [ ] Final review against spec §6.3 + §4.1 + API §12. Commit.

---

## Done when

A user can hold a cited, multi-turn conversation over their mailbox, files included, generated
locally by default (Ollama/LAN) with Anthropic as a disclosed opt-in, citations deep-linking via
`box://`, the index staying current as mail arrives — and, once the broker exists, the same
experience delivered by an Index/Embedder provider + a netless-of-mail AI consumer.
