# Spec — Phase 1: "Ask your mail" (local semantic search + Claude assistant)

Status: approved design (2026-06-09)
Branch: `feat/assistant-phase1-rag`

## 1. Goal

Add **two independent, opt-in tiers** to the mbox viewer, built on a shared local
retrieval layer:

1. **Semantic search (fully local, no egress).** Meaning-based search over message
   bodies **and** attachment text, hybrid-ranked with the existing FTS5 keyword index.
   Needs no API key and sends nothing off the machine — it's a standalone upgrade to
   the current keyword-only search.
2. **Assistant (chat).** A multi-turn, retrieval-grounded conversational assistant
   that answers questions with inline citations back to the source emails. Built
   **on top of** the semantic layer; generation runs on Claude, so it requires an API
   key and sends only retrieved snippets off the machine.

This is Phase 1 of the larger "personal assistant" direction: **read-only retrieval**
(semantic search) and **read-only Q&A** (chat). No drafting, no agents/tools, no
sending, no live mail sync — those are later phases.

Both tiers are **off by default**. With both off, the application behaves exactly as
it does today (fully local, keyword search only, no chat UI, no extra background work).

## 2. The two-tier model (key structural decision)

The retrieval layer (embeddings + `sqlite-vec` + hybrid ranking) has value **on its
own** as local semantic search — it does not depend on Claude. Generation is a second
capability stacked on top. The tiers are therefore gated independently:

| Tier | Env to activate | Key / egress? | Tier-specific deps | What the user gets |
|---|---|---|---|---|
| **Semantic search** | `SEMANTIC_SEARCH=1` | **No — fully local** | `fastembed`, `sqlite-vec` | Meaning-based + hybrid search in the existing search box |
| **Assistant (chat)** | `ASSISTANT_ENABLED=1` **and** `ANTHROPIC_API_KEY` | Yes (retrieved snippets only) | `anthropic` (small) | Multi-turn cited answers — **requires** the semantic tier |

**Activation rules** (computed in `Settings`):

- `assistant_active = assistant_enabled AND anthropic_api_key is set`
- `semantic_active  = semantic_search_enabled OR assistant_active`
  (the assistant needs retrieval, so enabling it implies the semantic layer)
- `ASSISTANT_ENABLED=1` with **no** API key → assistant treated as **off** (fail safe,
  logged at startup); the semantic layer follows its own flag.

The background passes (chunk + embed) gate on **`semantic_active`** — they are useful
without Claude. The chat UI/endpoints gate on **`assistant_active`**.

## 3. Architecture decisions (settled during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Embeddings location | **Configurable; in-container CPU default** | Self-contained/Dockerized ethos. Default `fastembed` (ONNX, CPU). Docker Desktop on macOS can't reach the M4 GPU, so in-container is CPU-only; an optional host-Ollama backend gives Metal acceleration for users who want it. |
| Generation model | **Configurable; `claude-sonnet-4-6` default** | Phase 1 chat is retrieval-grounded Q&A — retrieval does the heavy lifting, the model reads + answers. Sonnet is strong here and ~40% cheaper than Opus. |
| Vector store | **`sqlite-vec` in the same DB** | One disposable SQLite file, one backup story; `store.py` stays the only SQLite touchpoint. |
| Retrieval | **Hybrid (vector KNN + FTS5 BM25), RRF fusion** | The FTS5 index already exists and nails exact names/IDs that pure vector retrieval misses. Serves both tiers. |
| Chat | **Multi-turn, session-only** | Conversational follow-ups; conversation lives in the browser session (cleared on reload). No server-side chat persistence in Phase 1. |
| Attachments | **Embedded, with a per-attachment chunk cap** | "Search/ask about your attachments" is a headline capability; the cap bounds embedding cost/time. |

## 4. Hardware context (the target machine)

MacBook Air M4, 16 GB unified memory, 8-core GPU, ~49 GB free disk, macOS 15.6.1.

- **Retrieval layer (embeddings + vectors): runs fully local, comfortably.** Small
  embedding models are tiny and fast; the corpus and all vectors never leave the Mac.
- **Generation:** routed to Claude (the assistant tier). Only retrieved snippets per
  question egress — never the corpus, never the embeddings.
- In-container embedding is **CPU-only** (Docker on macOS has no GPU passthrough), so
  the one-time embed pass is a slower background job. Acceptable; it is resumable and
  the app stays usable while it runs.

## 5. Feature gating (behavior per state)

- **Both off (default):** unchanged app. Keyword search only; no semantic toggle, no
  chat tab. `GET /api/capabilities` → `{semantic:{enabled:false}, assistant:{enabled:false}}`.
  No background passes; no new code loaded at runtime.
- **Semantic on, assistant off:** chunk + embed passes run; the search box gains a
  semantic/hybrid toggle (disabled with a "building… N%" hint until ready). No chat tab.
  Still **zero egress, no API key**.
- **Assistant on:** implies semantic on (passes run). Chat "Ask" tab appears; semantic
  search also available. Only retrieved snippets egress to Anthropic.
- **`ASSISTANT_ENABLED=1` but no key:** assistant off + startup warning; semantic layer
  follows `SEMANTIC_SEARCH`.

## 6. Configuration (`config.py` `Settings`)

New fields (all from env; 3.9-compatible annotations — `Optional[...]`, never `X | None`):

| Field | Env var | Default | Notes |
|---|---|---|---|
| `semantic_search_enabled` | `SEMANTIC_SEARCH` | `False` | `"1"/"true"/"yes"` → true |
| `assistant_enabled` | `ASSISTANT_ENABLED` | `False` | as above |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `None` | required for `assistant_active` |
| `gen_model` | `ASSISTANT_MODEL` | `claude-sonnet-4-6` | passed verbatim to the SDK |
| `embed_backend` | `EMBED_BACKEND` | `local` | `local` \| `ollama` |
| `embed_model` | `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | model id for the chosen backend |
| `ollama_url` | `OLLAMA_URL` | `http://host.docker.internal:11434` | only used when backend=`ollama` |

`Settings` exposes `assistant_active` and `semantic_active` helpers (§2) used by routes
and the background-pass launcher.

## 7. Data model (additions to the single SQLite file; all in `store.py`)

```sql
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  kind TEXT NOT NULL,          -- 'body' | 'attachment'
  ord INTEGER NOT NULL,        -- chunk order within (message, kind, source)
  source_idx INTEGER,          -- attachment idx for kind='attachment', NULL for body
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_message ON chunks(message_id);

-- sqlite-vec virtual table; dimension N is fixed at creation from the embed model.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
  chunk_id INTEGER PRIMARY KEY,
  embedding float[N]
);
```

- `meta` records `embed_model`, `embed_dim`, `embed_backend`. If the configured embed
  model/dim differs from what's stored, **vectors** are cleared and the embed pass
  re-runs (chunk text is preserved — only re-vectorize).
- `chunks`/`vec_chunks` are created unconditionally (empty when semantic is off) so
  schema creation is uniform; they are **populated only** when `semantic_active`.
- `SCHEMA_VERSION`: see Open question O1 — bump to 5 vs. gate work on table-emptiness
  and leave at 4 (depends on whether `index_is_current` would force a full re-scan on a
  purely additive-table bump). The core message index is **not** rebuilt either way.

### `store.py` additions (still the only SQLite touchpoint)

- `add_chunk(message_id, kind, ord, source_idx, text) -> chunk_id`
- `iter_messages_for_chunking()` — yields `(id, offset, length)` for the chunk pass
- `count_chunks()`, `count_messages()` (exists) — for progress
- `chunks_without_vectors(limit)` — `(chunk_id, text)` batch for the embed pass
- `add_vector(chunk_id, embedding)`
- `knn_search(embedding, k)` — vector KNN → `[(chunk_id, message_id, distance)]`
- `search_fts_messages(query, k)` — BM25 message ids (reuse existing `_fts_query`)
- `get_chunk_text(chunk_id)` / `get_chunks_for_messages(ids)` — snippet hydration
- `clear_vectors()` — for embed-model change
- `embed_meta_get/set()` — stored model/dim/backend
- `clear()` extended to also empty `chunks` + `vec_chunks`

## 8. Background passes (only when `semantic_active`; resumable; report progress)

Kept **separate from the core indexer** (`build_index`) so indexer contracts (#4) are
untouched and the app serves immediately while these run on a daemon thread.

1. **Chunk pass** (`embed_index.build_chunks`)
   - Skips if `count_chunks() > 0` (idempotent/resumable; full rebuild via `clear`).
   - For each message: read bytes (`reader.read_message`), get body text (the indexer's
     body path) and attachment text (`extract.extract_text` over `reader.iter_attachments`).
   - Chunk via `chunker.iter_chunks`: ~512-token windows, ~64 overlap (token≈chars/4
     heuristic; no tokenizer dependency). **Per-attachment cap** (default 20 chunks) to
     bound cost; body uncapped but windowed.
   - Each chunk's stored/returned text is prefixed with a compact header
     (`Subject · From · Date`) so retrieved snippets carry context.
   - Writes `chunks` rows; commits every N for bounded WAL growth.

2. **Embed pass** (`embed_index.build_embeddings`)
   - Loops `chunks_without_vectors(batch)` → `embed.embed_texts(batch)` →
     `add_vector(...)` until none remain. Incremental/resumable.

Progress surfaces via `GET /api/capabilities` (§10): per-tier `enabled`, `ready`, and
build counts. `semantic ready = semantic_active AND chunks built AND no chunks awaiting
vectors`. A `BaseException` guard ensures the passes never wedge status at "building".

## 9. New modules (one responsibility each)

| File | Responsibility | Depends on |
|---|---|---|
| `embed.py` | Embedding behind one interface: `Embedder.embed_texts(list[str]) -> list[list[float]]`, `.dim`, `.model_name`. `LocalEmbedder` (fastembed/ONNX, CPU), `OllamaEmbedder` (HTTP to `ollama_url`). Lazy heavy imports. **No SQLite.** | fastembed (local) / httpx (ollama) |
| `chunker.py` | Pure text → windowed chunks with header prefix + per-attachment cap. **No I/O.** | — |
| `retrieve.py` | Hybrid retrieval shared by **both tiers**: vector KNN (`store.knn_search`) + FTS5 BM25 (`store.search_fts_messages`), **reciprocal-rank fusion** at message level. Two entry points: `search(query, limit)` → ranked `messages` for semantic search; `retrieve_context(query, budget)` → snippet set (best chunk(s) per message, token-budgeted) for the assistant. | store, embed |
| `assistant.py` | Build the messages array (system prompt + conversation history + current question with retrieved context), call Claude via the official `anthropic` SDK **streaming**, yield text. Anthropic client **injected** (testable, no network in tests). Formats/forwards citations. | anthropic, retrieve |

`embed_index.py` orchestrates the two passes (depends on store, reader, extract,
chunker, embed). `api.py` wires routes. `config.py` holds the new settings.

## 10. Generation contract (assistant tier)

- **Model:** `settings.gen_model` (default `claude-sonnet-4-6`), `max_tokens` ~1024,
  streaming via the SDK's `messages.stream` helper. Adaptive thinking left off for
  Phase 1 (straightforward grounded Q&A; keeps latency/cost low). Configurable later.
- **System prompt:** answer **only** from the provided email context; cite sources
  inline as `[#<id>]` where `<id>` is the internal integer `messages.id` (the same id
  the viewer opens via `/api/messages/{id}` — NOT the RFC `Message-ID` header), using
  only the ids present in the context block; if the answer is not in the provided mail,
  say so plainly rather than guessing; be concise.
- **Per-turn shape:** prior conversation turns + a final user turn = the new question
  followed by a `Context:` block of retrieved snippets, each labeled with its `#<id>`.
  Retrieval re-runs every turn on the latest question; context attaches to the current
  turn only (history stays lean).
- **Citations out:** the stream contains `[#id]` markers inline (id = integer
  `messages.id`); the response also carries the ordered source list (subject/from/date
  per id) so the UI can render citation chips even before parsing markers.

## 11. API (in `api.py`)

- `GET /api/capabilities` → `{semantic:{enabled, ready, chunks_done, chunks_total,
  vectors_done, vectors_total}, assistant:{enabled, ready, model}}`. Always 200; drives
  the UI (whether to show the semantic toggle and the Ask tab). Replaces the per-tier
  status endpoints with one capability probe.
- **Semantic search** — extend the existing search path with a `mode` param
  (`keyword` default | `hybrid`). `mode=hybrid` is honored only when semantic is
  `ready`; otherwise it falls back to keyword (so search always works during the build).
  Hybrid mode calls `retrieve.search`. Gated by `semantic_active`.
- `POST /api/assistant/chat` → body `{messages:[{role,content}...]}` (the browser's
  running conversation, last entry = new user question). **Streaming** answer tokens.
  When semantic isn't `ready`, returns a friendly "still building (X%)" payload instead
  of calling Claude. Gated by `assistant_active` (else 404). Per-turn source list emitted
  as a trailing JSON line / header the client reads.

## 12. Frontend (`static/`)

- **Capabilities probe** on load (`/api/capabilities`) decides what to show.
- **Semantic search:** when the semantic tier is present, the search box gains a small
  **"Semantic" / "Keyword" toggle**. Semantic ranks results by hybrid retrieval; the
  same result list/rendering is reused. While the embed passes run, the toggle shows a
  "building… N%" hint and stays on keyword.
- **Ask tab:** a third top-level tab beside Folders / Files, shown **only when the
  assistant tier is active**. Chat transcript with streamed answers; `[#id]` markers
  render as clickable citation chips that open the cited message in the existing reader
  pane. While building, show "Building knowledge base… N%" and disable input.
- All email-controlled strings injected via `innerHTML` are `escapeHtml`'d (main
  document is not sandboxed — existing project rule). Conversation is session-only
  (in-memory in `app.js`, cleared on reload).

## 13. Security / privacy

- **Default (both off): unchanged.** No egress, no new background work, no chat UI.
- **Semantic tier: fully local.** Embeddings, vectors, and all retrieval stay on the
  machine. No API key, no network egress whatsoever.
- **Assistant tier:** only the retrieved snippets for the current question (plus prior
  chat turns) are sent to Anthropic. The corpus and the embeddings never leave the
  machine. Anthropic does not train on API inputs by default.
- The localhost-only bind + no-auth posture is unchanged and matters more here; the
  README security note is extended to cover both tiers and the API key.
- `ANTHROPIC_API_KEY` is read from the environment only; never logged, never written to
  the index, never returned by any endpoint.

## 14. Dependencies (`requirements.txt`)

Split by tier:

- **Semantic tier (local):** `fastembed` (pulls `onnxruntime`), `sqlite-vec`.
- **Assistant tier:** `anthropic` (small).

All are added to the image, but heavy imports are **lazy** so a deployment with both
tiers off loads none of them at runtime. The local embedding model (~130 MB) downloads
on first use of the embed pass (Open question O2: pre-pull in Dockerfile vs. lazy
first-use; recommendation = lazy, documented). Image grows a few hundred MB when
installed. *(Optional lean-default variant — splitting the embedding stack into a
separate requirements file / build arg so the base image stays slim — is noted as a
possible follow-up, not Phase 1.)*

## 15. Testing (TDD; real artifacts where practical)

- `chunker`: window/overlap/cap/header behavior on real strings.
- `store`: vector round-trip (`add_vector`/`knn_search` ordering), `chunks_without_vectors`,
  `clear_vectors`, hybrid helper queries — against a real temp `sqlite-vec` DB.
- `retrieve`: RRF fusion ranking + budget enforcement with a **deterministic fake
  embedder** and seeded chunks/vectors; both `search` and `retrieve_context` entry points.
- `embed`: fake embedder for logic; **one optional integration test** exercises real
  `fastembed` (skipped if the model can't be fetched offline).
- `assistant`: **injected fake Claude client** — assert prompt assembly (system rules,
  context block, history), streaming passthrough, and citation/source formatting. No network.
- `api`: `TestClient` — `/api/capabilities` shape per state; search `mode=hybrid`
  fallback-to-keyword while building and hybrid results when ready (gated); chat gating
  (disabled → 404), streamed chat with the fake client, and the "not ready yet" path.
- `embed_index`: chunk + embed passes over the existing `sample_mbox` fixture with a
  fake embedder; resumability (re-run is a no-op) and `clear_vectors` re-embed.

Every module gets a matching `tests/test_*.py`, per project convention.

## 16. Out of scope (Phase 1)

Drafting/writing-in-your-voice, agent tools/function-calling, sending mail, live mail
sync, server-side chat persistence, reranker models, advanced query UI, prompt caching,
the optional lean-default image split. These are Phases 2–4 / follow-ups.

## 17. Open questions (resolve during planning)

- **O1:** Bump `SCHEMA_VERSION` to 5, or gate the new work on table-emptiness and leave
  it at 4 — depends on whether `index_is_current` would force a full re-scan on an
  additive-table bump. Inspect `indexer.index_is_current` before deciding.
- **O2:** Where the fastembed model is fetched — pre-pull in the Dockerfile (bigger
  image, works offline) vs. lazy first-use download (smaller image, needs network on
  first enable). Recommendation: lazy first-use, documented in the README.
- **O3:** Default per-attachment chunk cap (start at 20) and chunk window/overlap
  (start at ~512/64 char-approx) — tune once we see real embed timings on the M4.
