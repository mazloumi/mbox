# Spec — Phase 1: "Ask your mail" (local RAG + Claude generation)

Status: approved design (2026-06-09)
Branch: `feat/assistant-phase1-rag`

## 1. Goal

Add an **opt-in** conversational assistant to the mbox viewer that answers questions
about the indexed mailbox — across message bodies **and** attachment text — with
inline citations back to the source emails. This is Phase 1 of the larger
"personal assistant" direction: **read-only, retrieval-grounded Q&A** with a
multi-turn chat experience. No drafting, no agents/tools, no sending, no live
mail sync — those are later phases.

The assistant is **off by default**. When off, the application behaves exactly as
it does today (fully local, zero egress, no chat UI, no extra background work).

## 2. Architecture decisions (settled during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Embeddings location | **Configurable; in-container CPU default** | Self-contained/Dockerized ethos. Default `fastembed` (ONNX, CPU). Docker Desktop on macOS can't reach the M4 GPU, so in-container is CPU-only; an optional host-Ollama backend gives Metal acceleration for users who want it. |
| Generation model | **Configurable; `claude-sonnet-4-6` default** | Phase 1 is retrieval-grounded Q&A — retrieval does the heavy lifting, the model reads + answers. Sonnet is strong here and ~40% cheaper than Opus. |
| Feature activation | **Opt-in at container start** | `ASSISTANT_ENABLED=1` + `ANTHROPIC_API_KEY` required. Keeps the privacy-first default; Claude is purely opt-in. |
| Vector store | **`sqlite-vec` in the same DB** | One disposable SQLite file, one backup story; `store.py` stays the only SQLite touchpoint. |
| Retrieval | **Hybrid (vector KNN + FTS5 BM25), RRF fusion** | The FTS5 index already exists and nails exact names/IDs that pure vector retrieval misses. |
| Chat | **Multi-turn, session-only** | Conversational follow-ups; conversation lives in the browser session (cleared on reload). No server-side chat persistence in Phase 1. |
| Attachments | **Embedded, with a per-attachment chunk cap** | "Ask about your attachments" is a headline capability; the cap bounds embedding cost/time. |

## 3. Hardware context (the target machine)

MacBook Air M4, 16 GB unified memory, 8-core GPU, ~49 GB free disk, macOS 15.6.1.

- **Retrieval layer (embeddings + vectors): runs fully local, comfortably.** Small
  embedding models are tiny and fast; the corpus and all vectors never leave the Mac.
- **Generation:** routed to Claude (hybrid). Only retrieved snippets per question
  egress — never the corpus, never the embeddings.
- Note: in-container embedding is **CPU-only** (Docker on macOS has no GPU passthrough),
  so the one-time embed pass is a slower background job. Acceptable; it is resumable
  and the app stays usable while it runs.

## 4. Feature gating

Effective-on = `assistant_enabled AND anthropic_api_key is set`.

- **Off (default):** chat tab hidden in the UI; `GET /api/assistant/status` returns
  `{"enabled": false}`; `POST /api/assistant/chat` returns `404`/disabled; the chunk
  and embed background passes never start; no new dependencies are loaded at runtime.
- **On:** chat tab visible; background passes run (§7); chat answers stream from Claude.

If `ASSISTANT_ENABLED=1` but no API key is present, the app logs a clear warning at
startup and treats the feature as **off** (fail safe, never half-on).

## 5. Configuration (`config.py` `Settings`)

New fields (all from env, 3.9-compatible annotations — `Optional[...]`, never `X | None`):

| Field | Env var | Default | Notes |
|---|---|---|---|
| `assistant_enabled` | `ASSISTANT_ENABLED` | `False` | `"1"/"true"/"yes"` → true |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `None` | required for effective-on |
| `gen_model` | `ASSISTANT_MODEL` | `claude-sonnet-4-6` | passed verbatim to the SDK |
| `embed_backend` | `EMBED_BACKEND` | `local` | `local` \| `ollama` |
| `embed_model` | `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | model id for the chosen backend |
| `ollama_url` | `OLLAMA_URL` | `http://host.docker.internal:11434` | only used when backend=`ollama` |

`create_app`/`Settings` expose an `assistant_active` helper (the AND above) used by
routes and the background-pass launcher.

## 6. Data model (additions to the single SQLite file; all in `store.py`)

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

- `meta` records `embed_model`, `embed_dim`, and an `embed_backend` marker. If the
  configured embed model/dim differs from what's stored, the **vectors** are cleared
  and the embed pass re-runs (chunk text is preserved — only re-vectorize).
- The `chunks`/`vec_chunks` tables are created unconditionally (empty when the
  assistant is off) so schema creation is uniform; they are **populated only** when
  the assistant is active.
- `SCHEMA_VERSION`: bump to **5**. Creating empty new tables does not require
  re-reading message bytes, but bumping keeps the staleness guard honest and forces
  `create_schema` to run the new `CREATE`s on existing indexes. The core message
  index is **not** rebuilt by this bump (the new tables are additive); the bump's
  practical effect is ensuring the new tables exist. *(Implementation note: confirm
  `index_is_current` does not needlessly re-scan on a pure additive-table bump; if it
  would, gate the chunk/embed work on table-emptiness instead and leave
  `SCHEMA_VERSION` at 4. Decide during the plan — see Open question O1.)*

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

## 7. Background passes (only when assistant active; resumable; report progress)

Kept **separate from the core indexer** (`build_index`) so indexer contracts (#4)
are untouched and the app serves immediately while these run on a daemon thread.

1. **Chunk pass** (`embed_index.build_chunks`)
   - Skips if `count_chunks() > 0` (idempotent/resumable; full rebuild via `clear`).
   - For each message: read bytes (`reader.read_message`), get body text
     (`reader.get_display_body` path used by the indexer) and attachment text
     (`extract.extract_text` over `reader.iter_attachments`).
   - Chunk via `chunker.iter_chunks`: ~512-token windows, ~64 overlap (token≈chars/4
     heuristic; no tokenizer dependency). **Per-attachment cap** (default 20 chunks)
     to bound cost; body uncapped but windowed.
   - Each chunk's stored/returned text is prefixed with a compact header
     (`Subject · From · Date`) so retrieved snippets carry context.
   - Writes `chunks` rows; commits every N for bounded WAL growth.

2. **Embed pass** (`embed_index.build_embeddings`)
   - Loops `chunks_without_vectors(batch)` → `embed.embed_texts(batch)` →
     `add_vector(...)` until none remain. Incremental/resumable.

Progress flows to a status holder (parallel to `IndexStatus`) and is read by
`/api/assistant/status`: `{enabled, active, model, embed_backend, chunks_total,
chunks_done, vectors_total, vectors_done, ready}`. `ready = active AND chunks built
AND no chunks awaiting vectors`.

A `BaseException` guard ensures the passes never wedge the status at "building".

## 8. New modules (one responsibility each)

| File | Responsibility | Depends on |
|---|---|---|
| `embed.py` | Embedding behind one interface: `Embedder.embed_texts(list[str]) -> list[list[float]]`, `.dim`, `.model_name`. `LocalEmbedder` (fastembed/ONNX, CPU), `OllamaEmbedder` (HTTP to `ollama_url`). Lazy heavy imports. **No SQLite.** | fastembed (local) / httpx (ollama) |
| `chunker.py` | Pure text → windowed chunks with header prefix + per-attachment cap. **No I/O.** | — |
| `retrieve.py` | Hybrid retrieval: vector KNN (`store.knn_search`) + FTS5 BM25 (`store.search_fts_messages`), **reciprocal-rank fusion** at message level, hydrate best chunk(s) per message, enforce a snippet/token budget. Returns ordered `Snippet(message_id, subject, from_addr, date, text)`. | store, embed |
| `assistant.py` | Build the messages array (system prompt + conversation history + current question with retrieved context), call Claude via the official `anthropic` SDK **streaming**, yield text. Anthropic client **injected** (testable, no network in tests). Formats/forwards citations. | anthropic, retrieve |

`embed_index.py` orchestrates the two passes (depends on store, reader, extract,
chunker, embed). `api.py` wires routes. `config.py` holds the new settings.

## 9. Generation contract

- **Model:** `settings.gen_model` (default `claude-sonnet-4-6`), `max_tokens` ~1024,
  streaming via the SDK's `messages.stream` helper. Adaptive thinking left off for
  Phase 1 (straightforward grounded Q&A; keeps latency/cost low). Configurable later.
- **System prompt:** answer **only** from the provided email context; cite sources
  inline as `[#<id>]` where `<id>` is the internal integer `messages.id` (the same id
  the viewer opens via `/api/messages/{id}` — NOT the RFC `Message-ID` header), using
  only the ids present in the context block; if the answer is not in the provided mail,
  say so plainly rather than guessing; be concise.
- **Per-turn shape:** prior conversation turns (user/assistant text) + a final user
  turn = the new question followed by a `Context:` block of the retrieved snippets,
  each labeled with its `#<message_id>`. Retrieval re-runs every turn on the latest
  question. Context is attached to the **current** turn only (history stays lean).
- **Citations out:** the stream contains `[#id]` markers inline (id = integer
  `messages.id`); the response also carries the ordered source list (subject/from/date
  per id) so the UI can render citation chips even before parsing markers.

## 10. API (in `api.py`, gated by `assistant_active`)

- `GET /api/assistant/status` → the status object in §7. Always 200; reports
  `enabled:false` when off so the frontend can decide to show the tab.
- `POST /api/assistant/chat` → body `{messages: [{role, content}...]}` (the browser's
  running conversation, last entry = new user question). Returns a **streaming**
  response of answer tokens. When `ready` is false, returns a friendly "still building
  the knowledge base (X%)" payload instead of calling Claude. When disabled, 404.
  Sources for the turn are emitted as a trailing JSON line / header the client reads.

## 11. Frontend (`static/`)

- A third top-level tab **"Ask"** beside Folders / Files, **hidden when
  `/api/assistant/status` reports disabled**.
- Chat transcript pane: user bubbles + streamed assistant answers. While the embed
  passes run, show a "Building knowledge base… N%" state and disable input.
- **Citations:** `[#id]` markers render as clickable chips; clicking opens that
  message in the existing reader pane (reuse the current message-open path). All
  email-controlled strings injected via `innerHTML` are `escapeHtml`'d (the main
  document is not sandboxed — existing project rule).
- Conversation is **session-only** (in-memory in `app.js`; cleared on reload).

## 12. Security / privacy

- **Default (off): unchanged.** No egress, no new background work, no chat UI.
- **On:** only the retrieved snippets for the current question (plus prior chat turns)
  are sent to Anthropic. The corpus and the embeddings never leave the machine.
  Anthropic does not train on API inputs by default.
- The localhost-only bind + no-auth posture is unchanged and matters more here; the
  README security note is extended to cover the assistant and the API key.
- `ANTHROPIC_API_KEY` is read from the environment only; never logged, never written
  to the index, never returned by any endpoint.

## 13. Dependencies (`requirements.txt`)

Add: `anthropic`, `sqlite-vec`, `fastembed` (pulls `onnxruntime`). The local
embedding model (~130 MB) downloads on first use of the embed pass; for the
container this can be pre-fetched at build time or on first enable (decide in plan —
Open question O2). Heavy imports are lazy so a disabled deployment loads none of them.
Image grows a few hundred MB when these are installed.

## 14. Testing (TDD; real artifacts where practical)

- `chunker`: window/overlap/cap/header behavior on real strings.
- `store`: vector round-trip (`add_vector`/`knn_search` ordering), `chunks_without_vectors`,
  `clear_vectors`, hybrid helper queries — against a real temp `sqlite-vec` DB.
- `retrieve`: RRF fusion ranking + budget enforcement with a **deterministic fake
  embedder** and seeded chunks/vectors.
- `embed`: fake embedder for logic; **one optional integration test** exercises real
  `fastembed` (skipped if the model can't be fetched offline).
- `assistant`: **injected fake Claude client** — assert prompt assembly (system rules,
  context block, history), streaming passthrough, and citation/source formatting. No network.
- `api`: `TestClient` — gating (disabled → 404 / `enabled:false`), status shape, a
  streamed chat with the fake client, and the "not ready yet" path.
- `embed_index`: chunk + embed passes over the existing `sample_mbox` fixture with a
  fake embedder; resumability (re-run is a no-op) and `clear_vectors` re-embed.

Every module gets a matching `tests/test_*.py`, per project convention.

## 15. Out of scope (Phase 1)

Drafting/writing-in-your-voice, agent tools/function-calling, sending mail, live mail
sync, server-side chat persistence, reranker models, advanced query UI, prompt
caching (can be added later as a pure optimization). These are Phases 2–4.

## 16. Open questions (resolve during planning)

- **O1:** Whether to bump `SCHEMA_VERSION` to 5 or gate the new work on table-emptiness
  and leave it at 4 — depends on whether `index_is_current` would force a full re-scan
  on an additive-table bump. Inspect `indexer.index_is_current` before deciding.
- **O2:** Where the fastembed model is fetched — pre-pull in the Dockerfile (bigger
  image, works offline) vs. lazy first-use download (smaller image, needs network on
  first enable). Recommendation: lazy first-use, documented in the README.
- **O3:** Default per-attachment chunk cap (start at 20) and chunk window/overlap
  (start at ~512/64 char-approx) — tune once we see real embed timings on the M4.
