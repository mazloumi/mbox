# "Ask your mail" — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two independent, opt-in tiers to the mbox viewer — local semantic search (fully on-device) and a Claude-backed multi-turn chat assistant — both built on a shared local retrieval layer over message bodies and attachment text.

**Architecture:** A local embedding model (in-container CPU default, optional host Ollama) turns each message/attachment chunk into a vector stored in `sqlite-vec` alongside the existing FTS5 index. Two decoupled, resumable background passes (chunk → embed) build the index when the semantic tier is active. Hybrid retrieval (vector KNN + FTS5 BM25, reciprocal-rank fusion) serves both semantic search and the assistant. The assistant streams cited answers from Claude (`claude-sonnet-4-6` default), sending only retrieved snippets off the machine. Everything is off by default.

**Tech Stack:** Python 3.9 (dev) / 3.12 (Docker), FastAPI, SQLite (WAL, thread-local conns), `sqlite-vec`, `fastembed` (ONNX/CPU), the official `anthropic` SDK, vanilla JS frontend (no build step).

**Spec:** `docs/superpowers/specs/2026-06-09-mbox-assistant-phase1-rag-design.md`

---

## File Structure

| File | Created / Modified | Responsibility |
|---|---|---|
| `src/mboxviewer/config.py` | Modify | New settings + `semantic_active`/`assistant_active` helpers |
| `requirements.txt` | Modify | Add `sqlite-vec`, `fastembed`, `anthropic`, `httpx` |
| `src/mboxviewer/chunker.py` | Create | Pure text → windowed chunks (header prefix, per-attachment cap) |
| `src/mboxviewer/store.py` | Modify | `chunks` table, lazy `vec_chunks` (sqlite-vec), vector + chunk methods |
| `src/mboxviewer/embed.py` | Create | Embedding backends behind one interface (local fastembed / ollama) |
| `src/mboxviewer/retrieve.py` | Create | Hybrid retrieval (RRF): `search()` + `retrieve_context()` |
| `src/mboxviewer/status.py` | Modify | Add `EmbedStatus` progress holder |
| `src/mboxviewer/embed_index.py` | Create | `build_chunks()` + `build_embeddings()` background passes |
| `src/mboxviewer/assistant.py` | Create | Prompt assembly, citation/context block, Claude streaming glue |
| `src/mboxviewer/api.py` | Modify | `/api/capabilities`, search `mode`, `/api/assistant/chat`, pass launch |
| `src/mboxviewer/static/index.html` | Modify | Ask tab, semantic toggle markup |
| `src/mboxviewer/static/app.js` | Modify | Capabilities probe, semantic toggle, chat UI + streaming |
| `src/mboxviewer/static/style.css` | Modify | Chat + toggle styles |
| `docker-compose.yml`, `.env.example` | Modify | Pass the new env vars |
| `README.md` | Modify | Document the two tiers + security |

**Conventions (do not break):** 3.9-compatible annotations (`Optional[...]`, never `X | None`); `store.py` is the only file that touches SQLite; heavy imports (`fastembed`, `anthropic`, `sqlite_vec`) are **lazy** so a both-tiers-off deployment loads none of them; every module gets a matching `tests/test_*.py`.

**Run tests with:** `.venv/bin/pytest` (pytest.ini sets `pythonpath = src`).

---

## Task 1: Config settings + activation helpers

**Files:**
- Modify: `src/mboxviewer/config.py`
- Test: `tests/test_config.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import importlib

from mboxviewer.config import Settings


def _settings(**kw):
    base = dict(mbox_path="/x.mbox", index_path="/i.db")
    base.update(kw)
    return Settings(**base)


def test_defaults_off():
    s = _settings()
    assert s.semantic_search_enabled is False
    assert s.assistant_enabled is False
    assert s.anthropic_api_key is None
    assert s.gen_model == "claude-sonnet-4-6"
    assert s.embed_backend == "local"
    assert s.semantic_active() is False
    assert s.assistant_active() is False


def test_assistant_needs_key():
    # enabled but no key -> assistant off, semantic follows its own flag
    s = _settings(assistant_enabled=True, anthropic_api_key=None)
    assert s.assistant_active() is False
    assert s.semantic_active() is False


def test_assistant_active_implies_semantic():
    s = _settings(assistant_enabled=True, anthropic_api_key="sk-ant-xyz")
    assert s.assistant_active() is True
    assert s.semantic_active() is True


def test_semantic_standalone():
    s = _settings(semantic_search_enabled=True)
    assert s.semantic_active() is True
    assert s.assistant_active() is False


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SEARCH", "1")
    monkeypatch.setenv("ASSISTANT_ENABLED", "yes")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    monkeypatch.setenv("ASSISTANT_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("EMBED_BACKEND", "ollama")
    import mboxviewer.config as cfg
    importlib.reload(cfg)
    s = cfg.load_settings()
    assert s.semantic_search_enabled is True
    assert s.assistant_enabled is True
    assert s.gen_model == "claude-opus-4-8"
    assert s.embed_backend == "ollama"
    assert s.assistant_active() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL (`Settings` has no `semantic_search_enabled`).

- [ ] **Step 3: Implement**

Replace `src/mboxviewer/config.py` with:

```python
import os
from dataclasses import dataclass
from typing import Optional


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    mbox_path: str
    index_path: str
    archive_dir: str = "/archive"
    # Display name for the mbox (the host filename). The container mount renames the file
    # to /data/mail.mbox, so basename(mbox_path) loses the real name; this preserves it.
    mbox_name: str = ""
    host: str = "0.0.0.0"
    port: int = 9000
    # --- Assistant / semantic-search tiers (both opt-in, off by default) ---
    semantic_search_enabled: bool = False
    assistant_enabled: bool = False
    anthropic_api_key: Optional[str] = None
    gen_model: str = "claude-sonnet-4-6"
    embed_backend: str = "local"          # "local" | "ollama"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    ollama_url: str = "http://host.docker.internal:11434"

    def assistant_active(self) -> bool:
        """Chat tier is on only when explicitly enabled AND a key is present."""
        return bool(self.assistant_enabled and self.anthropic_api_key)

    def semantic_active(self) -> bool:
        """Retrieval tier; the assistant requires it, so it implies semantic."""
        return bool(self.semantic_search_enabled or self.assistant_active())


def load_settings() -> Settings:
    return Settings(
        mbox_path=os.environ.get("MBOX_PATH", "/data/mail.mbox"),
        index_path=os.environ.get("INDEX_PATH", "/index/index.db"),
        archive_dir=os.environ.get("ARCHIVE_DIR", "/archive"),
        mbox_name=os.environ.get("MBOX_NAME", ""),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "9000")),
        semantic_search_enabled=_flag(os.environ.get("SEMANTIC_SEARCH")),
        assistant_enabled=_flag(os.environ.get("ASSISTANT_ENABLED")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        gen_model=os.environ.get("ASSISTANT_MODEL", "claude-sonnet-4-6"),
        embed_backend=os.environ.get("EMBED_BACKEND", "local"),
        embed_model=os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/config.py tests/test_config.py
git commit -m "feat(config): two-tier assistant settings + activation helpers"
```

---

## Task 2: Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Install the new runtime deps into the dev venv**

Run:
```bash
.venv/bin/pip install sqlite-vec fastembed anthropic httpx
```
Expected: installs succeed (fastembed pulls `onnxruntime`, `numpy`, `tokenizers`).

- [ ] **Step 2: Capture the resolved versions**

Run:
```bash
.venv/bin/pip show sqlite-vec fastembed anthropic httpx | grep -E "^(Name|Version)"
```
Expected: four `Name`/`Version` pairs. Note the versions for the next step.

- [ ] **Step 3: Pin them in `requirements.txt`**

Append these lines to `requirements.txt`, substituting the exact versions captured in Step 2 (keep the existing lines unchanged):

```
# --- Assistant / semantic-search tiers (imported lazily; only loaded when enabled) ---
sqlite-vec==<resolved>
fastembed==<resolved>
anthropic==<resolved>
httpx==<resolved>
```

- [ ] **Step 4: Verify imports work**

Run:
```bash
.venv/bin/python -c "import sqlite_vec, fastembed, anthropic, httpx; print('ok')"
```
Expected: `ok`.

- [ ] **Step 5: Confirm the existing suite still passes**

Run: `.venv/bin/pytest -q`
Expected: all existing tests PASS (new deps don't affect existing code).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt
git commit -m "build: add sqlite-vec, fastembed, anthropic, httpx (assistant tiers)"
```

---

## Task 3: `chunker.py` — text → windowed chunks

**Files:**
- Create: `src/mboxviewer/chunker.py`
- Test: `tests/test_chunker.py`

A "token ≈ 4 chars" heuristic avoids a tokenizer dependency. Defaults: window 2000 chars, overlap 200, per-attachment cap 20 chunks. Each chunk text is prefixed with a one-line header so retrieved snippets carry context.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunker.py
from mboxviewer.chunker import iter_chunks, Chunk


def test_short_body_single_chunk_with_header():
    chunks = list(iter_chunks(subject="Hi", from_addr="a@x.com", date="2024-01-01",
                              body="hello world", attachments=[]))
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert c.kind == "body"
    assert c.ord == 0
    assert c.source_idx is None
    assert c.text.startswith("Hi · a@x.com · 2024-01-01\n")
    assert "hello world" in c.text


def test_long_body_windows_with_overlap():
    body = "x" * 4500  # > 2 windows at 2000/200
    chunks = [c for c in iter_chunks("S", "f", "d", body, []) if c.kind == "body"]
    assert len(chunks) == 3            # 0..2000, 1800..3800, 3600..4500
    assert [c.ord for c in chunks] == [0, 1, 2]
    # overlap: window 1 starts 200 chars before window 0 ends
    assert chunks[1].text.count("x") == 2000


def test_attachments_chunked_and_capped():
    big = "y" * 100000  # would be ~53 windows; cap to 20
    atts = [(3, "report.pdf", big)]   # (idx, filename, extracted_text)
    chunks = [c for c in iter_chunks("S", "f", "d", "", atts) if c.kind == "attachment"]
    assert len(chunks) == 20
    assert all(c.source_idx == 3 for c in chunks)
    assert chunks[0].text.startswith("S · f · d · report.pdf\n")


def test_empty_text_skipped():
    atts = [(0, "blank.txt", "   "), (1, "ok.txt", "real content")]
    kinds = [(c.kind, c.source_idx) for c in iter_chunks("S", "f", "d", "", atts)]
    assert ("attachment", 0) not in kinds   # whitespace-only attachment produces no chunk
    assert ("attachment", 1) in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chunker.py -v`
Expected: FAIL (no module `chunker`).

- [ ] **Step 3: Implement**

```python
# src/mboxviewer/chunker.py
"""Pure text → chunks. No I/O. A chunk is a window of text prefixed with a compact
header so a retrieved snippet still says which message/attachment it came from."""
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

WINDOW = 2000          # chars per chunk (~512 tokens at ~4 chars/token)
OVERLAP = 200          # chars of overlap between consecutive windows
ATTACH_CAP = 20        # max chunks per attachment (bounds embedding cost)


@dataclass
class Chunk:
    kind: str                 # "body" | "attachment"
    ord: int                  # order within (message, kind, source)
    source_idx: Optional[int] # attachment idx for kind="attachment", else None
    text: str                 # header line + windowed body text


def _windows(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= WINDOW:
        return [text]
    out = []
    step = WINDOW - OVERLAP
    start = 0
    while start < len(text):
        out.append(text[start:start + WINDOW])
        start += step
    return out


def iter_chunks(subject: str, from_addr: str, date: str, body: str,
                attachments: Iterable[Tuple[int, str, str]]) -> Iterator[Chunk]:
    """Yield Chunks for one message.

    `attachments` is an iterable of (idx, filename, extracted_text).
    """
    head = " · ".join(p for p in (subject, from_addr, date) if p)
    for i, win in enumerate(_windows(body)):
        yield Chunk("body", i, None, f"{head}\n{win}")
    for idx, filename, text in attachments:
        wins = _windows(text)[:ATTACH_CAP]
        ahead = " · ".join(p for p in (subject, from_addr, date, filename) if p)
        for i, win in enumerate(wins):
            yield Chunk("attachment", i, idx, f"{ahead}\n{win}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_chunker.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/chunker.py tests/test_chunker.py
git commit -m "feat(chunker): windowed message/attachment chunking with header prefix"
```

---

## Task 4: `store.py` — chunks table + sqlite-vec vector methods

**Files:**
- Modify: `src/mboxviewer/store.py`
- Test: `tests/test_store_vectors.py`

Add the plain `chunks` table to the always-run `SCHEMA` (additive — no `SCHEMA_VERSION` bump, so existing indexes are NOT re-scanned). The `vec_chunks` vec0 table needs the `sqlite-vec` extension loaded, so it is created lazily via `ensure_vector_schema(dim)`. `Store(enable_vectors=True)` makes each thread-local connection load the extension.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_vectors.py
import pytest

from mboxviewer.store import Store


@pytest.fixture
def vstore(tmp_path):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    s.ensure_vector_schema(3)
    return s


def _msg(s):
    return s.add_message(0, 1, "<m>", "Subj", "a@x.com", "b@x.com", "2024-01-01", "raw")


def test_chunk_roundtrip_and_pending_vectors(vstore):
    mid = _msg(vstore)
    c1 = vstore.add_chunk(mid, "body", 0, None, "hello")
    c2 = vstore.add_chunk(mid, "attachment", 0, 2, "world")
    assert vstore.count_chunks() == 2
    pending = vstore.chunks_without_vectors(10)
    assert {cid for cid, _ in pending} == {c1, c2}


def test_knn_orders_by_distance(vstore):
    mid = _msg(vstore)
    a = vstore.add_chunk(mid, "body", 0, None, "a")
    b = vstore.add_chunk(mid, "body", 1, None, "b")
    vstore.add_vector(a, [1.0, 0.0, 0.0])
    vstore.add_vector(b, [0.0, 1.0, 0.0])
    # query closest to `a`
    hits = vstore.knn_search([0.9, 0.1, 0.0], 2)
    assert [h[0] for h in hits] == [a, b]          # chunk_id order
    assert all(h[1] == mid for h in hits)          # message_id mapped
    assert vstore.chunks_without_vectors(10) == [] # both embedded now


def test_clear_vectors_keeps_chunk_text(vstore):
    mid = _msg(vstore)
    c = vstore.add_chunk(mid, "body", 0, None, "keep me")
    vstore.add_vector(c, [1.0, 2.0, 3.0])
    vstore.clear_vectors()
    assert vstore.count_chunks() == 1                       # text preserved
    assert vstore.chunks_without_vectors(10) == [(c, "keep me")]


def test_embed_meta_roundtrip(vstore):
    vstore.embed_meta_set("BAAI/bge-small-en-v1.5", 384, "local")
    assert vstore.embed_meta_get() == ("BAAI/bge-small-en-v1.5", 384, "local")


def test_search_fts_messages(vstore):
    mid = _msg(vstore)
    vstore.add_fts(mid, "Subj", "a@x.com", "b@x.com", "the quick brown fox", "")
    assert vstore.search_fts_messages("quick", 5) == [mid]
    assert vstore.search_fts_messages("zzz", 5) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_store_vectors.py -v`
Expected: FAIL (`Store.__init__` takes no `enable_vectors`).

- [ ] **Step 3: Implement — add the `chunks` table to `SCHEMA`**

In `src/mboxviewer/store.py`, inside the `SCHEMA` string, add the `chunks` table just before the `messages_fts` virtual table:

```python
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  kind TEXT NOT NULL,
  ord INTEGER NOT NULL,
  source_idx INTEGER,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_message ON chunks(message_id);
```

- [ ] **Step 4: Implement — vector-aware connection + methods**

Replace `Store.__init__` and the `conn` property, and add the new methods. New `__init__`:

```python
    def __init__(self, db_path: str, enable_vectors: bool = False):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()
        self._enable_vectors = enable_vectors
```

New `conn` property (loads sqlite-vec per thread-local connection when enabled):

```python
    @property
    def conn(self):
        """A SQLite connection unique to the calling thread (lazily opened)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            if self._enable_vectors:
                import sqlite_vec  # lazy: only when the semantic tier is active
                c.enable_load_extension(True)
                sqlite_vec.load(c)
                c.enable_load_extension(False)
            self._local.conn = c
        return c
```

Add these methods anywhere after `add_fts` (use `sqlite_vec.serialize_float32` for vectors):

```python
    # --- chunks + vectors (semantic tier) -------------------------------------
    def ensure_vector_schema(self, dim):
        """Create the sqlite-vec virtual table at the embedding dimension. Idempotent."""
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
            "chunk_id INTEGER PRIMARY KEY, embedding float[%d])" % int(dim))
        self.conn.commit()

    def add_chunk(self, message_id, kind, ord, source_idx, text):
        cur = self.conn.execute(
            "INSERT INTO chunks(message_id,kind,ord,source_idx,text) VALUES(?,?,?,?,?)",
            (message_id, kind, ord, source_idx, text))
        return cur.lastrowid

    def count_chunks(self):
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def iter_messages_for_chunking(self):
        return self.conn.execute(
            "SELECT id, offset, length FROM messages ORDER BY id").fetchall()

    def chunks_without_vectors(self, limit):
        rows = self.conn.execute(
            "SELECT c.id AS id, c.text AS text FROM chunks c "
            "LEFT JOIN vec_chunks v ON v.chunk_id = c.id "
            "WHERE v.chunk_id IS NULL ORDER BY c.id LIMIT ?", (limit,)).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def add_vector(self, chunk_id, embedding):
        import sqlite_vec
        self.conn.execute(
            "INSERT INTO vec_chunks(chunk_id, embedding) VALUES(?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(list(embedding))))

    def knn_search(self, embedding, k):
        """Return [(chunk_id, message_id, distance)] nearest to `embedding`."""
        import sqlite_vec
        rows = self.conn.execute(
            "WITH knn AS ("
            "  SELECT chunk_id, distance FROM vec_chunks "
            "  WHERE embedding MATCH ? ORDER BY distance LIMIT ?) "
            "SELECT knn.chunk_id AS chunk_id, c.message_id AS message_id, "
            "       knn.distance AS distance "
            "FROM knn JOIN chunks c ON c.id = knn.chunk_id ORDER BY knn.distance",
            (sqlite_vec.serialize_float32(list(embedding)), k)).fetchall()
        return [(r["chunk_id"], r["message_id"], r["distance"]) for r in rows]

    def search_fts_messages(self, query, k):
        """BM25-ranked message ids for `query` (reuses the keyword FTS index)."""
        match = _fts_query(query)
        if not match:
            return []
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (match, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["rowid"] for r in rows]

    def get_chunk_text(self, chunk_id):
        row = self.conn.execute(
            "SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return row["text"] if row else None

    def clear_vectors(self):
        """Drop all vectors (e.g. embed-model change); chunk text is preserved."""
        self.conn.execute("DROP TABLE IF EXISTS vec_chunks")
        self.conn.commit()

    def embed_meta_set(self, model, dim, backend):
        self.set_meta("embed_model", model)
        self.set_meta("embed_dim", str(int(dim)))
        self.set_meta("embed_backend", backend)
        self.conn.commit()

    def embed_meta_get(self):
        model = self.get_meta("embed_model")
        dim = self.get_meta("embed_dim")
        backend = self.get_meta("embed_backend")
        if model is None or dim is None:
            return None
        return (model, int(dim), backend)
```

- [ ] **Step 5: Implement — extend `clear()` to drop chunks + vectors**

In `Store.clear()`, add to the `executescript` string the deletion of chunks, and drop the vec table after. Change the `executescript(...)` call to include `"DELETE FROM chunks;"` and after the `messages_fts` delete-all line add:

```python
        self.conn.execute("DROP TABLE IF EXISTS vec_chunks")
        self.conn.commit()
```

(Leave the existing `messages_fts` `delete-all` line as-is.)

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_store_vectors.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Confirm existing store/api tests still pass (default path, vectors off)**

Run: `.venv/bin/pytest tests/test_store.py tests/test_api.py -q`
Expected: PASS — `enable_vectors` defaults to `False`, so existing behavior is unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/mboxviewer/store.py tests/test_store_vectors.py
git commit -m "feat(store): chunks table + lazy sqlite-vec vectors (hybrid retrieval)"
```

---

## Task 5: `embed.py` — embedding backends

**Files:**
- Create: `src/mboxviewer/embed.py`
- Test: `tests/test_embed.py`

One interface, two backends, a factory. Heavy imports are lazy. Tests use a fake; one real `fastembed` test is opt-in (skipped unless `MBOX_TEST_FASTEMBED=1`, since it downloads a model).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embed.py
import os
import pytest

from mboxviewer import embed
from mboxviewer.config import Settings


def test_factory_local_by_default():
    s = Settings(mbox_path="x", index_path="y")
    e = embed.make_embedder(s)
    assert isinstance(e, embed.LocalEmbedder)
    assert e.model_name == "BAAI/bge-small-en-v1.5"


def test_factory_ollama():
    s = Settings(mbox_path="x", index_path="y", embed_backend="ollama",
                 embed_model="nomic-embed-text", ollama_url="http://h:1")
    e = embed.make_embedder(s)
    assert isinstance(e, embed.OllamaEmbedder)
    assert e.model_name == "nomic-embed-text"


def test_factory_rejects_unknown_backend():
    s = Settings(mbox_path="x", index_path="y", embed_backend="bogus")
    with pytest.raises(ValueError):
        embed.make_embedder(s)


@pytest.mark.skipif(os.environ.get("MBOX_TEST_FASTEMBED") != "1",
                    reason="set MBOX_TEST_FASTEMBED=1 to run the real fastembed download")
def test_local_embedder_real():
    e = embed.LocalEmbedder("BAAI/bge-small-en-v1.5")
    vecs = e.embed_texts(["hello world", "goodbye"])
    assert len(vecs) == 2
    assert e.dim == len(vecs[0]) == 384
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_embed.py -v`
Expected: FAIL (no module `embed`).

- [ ] **Step 3: Implement**

```python
# src/mboxviewer/embed.py
"""Embedding backends behind one interface. Heavy deps are imported lazily so a
deployment with the semantic tier off never loads them."""
from typing import List


class LocalEmbedder:
    """fastembed (ONNX, CPU). The model loads on first embed and is cached."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None
        self._dim = None

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding  # lazy
            self._model = TextEmbedding(model_name=self.model_name)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        self._ensure()
        return [list(map(float, v)) for v in self._model.embed(list(texts))]

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_texts(["dimension probe"])[0])
        return self._dim


class OllamaEmbedder:
    """Host-side Ollama via HTTP (Metal-accelerated). Optional backend."""

    def __init__(self, model_name: str, url: str):
        self.model_name = model_name
        self.url = url.rstrip("/")
        self._dim = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        import httpx  # lazy
        out = []
        with httpx.Client(timeout=120) as client:
            for t in texts:
                r = client.post(f"{self.url}/api/embeddings",
                                json={"model": self.model_name, "prompt": t})
                r.raise_for_status()
                out.append([float(x) for x in r.json()["embedding"]])
        return out

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_texts(["dimension probe"])[0])
        return self._dim


def make_embedder(settings):
    backend = settings.embed_backend
    if backend == "local":
        return LocalEmbedder(settings.embed_model)
    if backend == "ollama":
        return OllamaEmbedder(settings.embed_model, settings.ollama_url)
    raise ValueError(f"unknown EMBED_BACKEND: {backend!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_embed.py -v`
Expected: PASS (3 run, 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/embed.py tests/test_embed.py
git commit -m "feat(embed): local fastembed + ollama backends behind one interface"
```

---

## Task 6: `retrieve.py` — hybrid retrieval (RRF)

**Files:**
- Create: `src/mboxviewer/retrieve.py`
- Test: `tests/test_retrieve.py`

Reciprocal-rank fusion over the vector ranking (best chunk distance per message) and the FTS BM25 ranking. `search()` returns ranked message rows for the search UI; `retrieve_context()` returns snippets (best chunk text per message) for the assistant, within a char budget.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retrieve.py
import pytest

from mboxviewer.store import Store
from mboxviewer import retrieve


class FakeEmbedder:
    """Deterministic 3-d vectors so KNN order is predictable in tests."""
    model_name = "fake"
    dim = 3

    def __init__(self, mapping):
        self._mapping = mapping  # text-substring -> vector

    def embed_texts(self, texts):
        out = []
        for t in texts:
            for key, vec in self._mapping.items():
                if key in t:
                    out.append(vec)
                    break
            else:
                out.append([0.0, 0.0, 0.0])
        return out


@pytest.fixture
def seeded(tmp_path):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    s.ensure_vector_schema(3)
    rows = {}
    for mid_text, vec, subj, body in [
        ("apple", [1, 0, 0], "Apple thread", "about apples and orchards"),
        ("banana", [0, 1, 0], "Banana thread", "yellow banana split"),
        ("cherry", [0, 0, 1], "Cherry thread", "cherry pie recipe"),
    ]:
        mid = s.add_message(0, 1, "<m>", subj, "a@x.com", "b@x.com", "2024-01-01", "raw")
        s.add_fts(mid, subj, "a@x.com", "b@x.com", body, "")
        cid = s.add_chunk(mid, "body", 0, None, f"{subj}\n{body}")
        s.add_vector(cid, [float(v) for v in vec])
        rows[mid_text] = mid
    return s, rows


def test_search_ranks_semantic_match_first(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"banana-query": [0.0, 1.0, 0.0]})
    results = retrieve.search(store, emb, "banana-query", limit=3)
    assert results[0]["id"] == rows["banana"]


def test_retrieve_context_returns_snippets_with_ids(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"cherry-query": [0.0, 0.0, 1.0]})
    snips = retrieve.retrieve_context(store, emb, "cherry-query", budget_chars=10000)
    assert snips[0].message_id == rows["cherry"]
    assert "cherry" in snips[0].text.lower()
    assert snips[0].subject == "Cherry thread"


def test_budget_caps_snippets(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"q": [1.0, 1.0, 1.0]})
    snips = retrieve.retrieve_context(store, emb, "apple banana cherry", budget_chars=50)
    total = sum(len(s.text) for s in snips)
    assert total <= 50 + 100   # budget honored (allow one snippet's slack)
    assert len(snips) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_retrieve.py -v`
Expected: FAIL (no module `retrieve`).

- [ ] **Step 3: Implement**

```python
# src/mboxviewer/retrieve.py
"""Hybrid retrieval shared by both tiers: vector KNN + FTS5 BM25, fused with
reciprocal-rank fusion (RRF). `search` feeds the search UI; `retrieve_context`
feeds the assistant."""
from dataclasses import dataclass
from typing import List, Optional

RRF_K = 60          # standard RRF damping constant
POOL = 50           # candidates pulled from each ranker before fusion


@dataclass
class Snippet:
    message_id: int
    subject: Optional[str]
    from_addr: Optional[str]
    date: Optional[str]
    text: str           # the best matching chunk's text


def _fused_message_order(store, embedder, query):
    """Return [(message_id, best_chunk_id_or_None)] ranked by RRF over vector+FTS."""
    qvec = embedder.embed_texts([query])[0]
    knn = store.knn_search(qvec, POOL)          # [(chunk_id, message_id, distance)]
    # Vector ranking at message level: first time a message appears = its best rank.
    vec_rank = {}
    best_chunk = {}
    for rank, (chunk_id, mid, _dist) in enumerate(knn):
        if mid not in vec_rank:
            vec_rank[mid] = rank
            best_chunk[mid] = chunk_id
    fts_ids = store.search_fts_messages(query, POOL)
    fts_rank = {mid: rank for rank, mid in enumerate(fts_ids)}

    scores = {}
    for mid, rank in vec_rank.items():
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    for mid, rank in fts_rank.items():
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)

    order = sorted(scores, key=lambda m: scores[m], reverse=True)
    return [(mid, best_chunk.get(mid)) for mid in order]


def search(store, embedder, query, limit):
    """Ranked message rows for the semantic search UI."""
    fused = _fused_message_order(store, embedder, query)[:limit]
    out = []
    for mid, _chunk in fused:
        row = store.get_message_row(mid)
        if row is not None:
            out.append(row)
    return out


def retrieve_context(store, embedder, query, budget_chars=12000, max_snippets=20):
    """Snippets (best chunk per message) for the assistant, within a char budget."""
    fused = _fused_message_order(store, embedder, query)
    snippets = []
    used = 0
    for mid, chunk_id in fused:
        if len(snippets) >= max_snippets:
            break
        text = store.get_chunk_text(chunk_id) if chunk_id is not None else None
        if not text:
            row = store.get_message_row(mid)
            text = (row["preview"] or "") if row is not None else ""
        if not text:
            continue
        if used and used + len(text) > budget_chars:
            break
        row = store.get_message_row(mid)
        snippets.append(Snippet(
            message_id=mid,
            subject=row["subject"] if row else None,
            from_addr=row["from_addr"] if row else None,
            date=row["date"] if row else None,
            text=text))
        used += len(text)
    return snippets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_retrieve.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/retrieve.py tests/test_retrieve.py
git commit -m "feat(retrieve): hybrid vector+FTS retrieval with RRF fusion"
```

---

## Task 7: `status.py` — `EmbedStatus` progress holder

**Files:**
- Modify: `src/mboxviewer/status.py`
- Test: `tests/test_embed_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embed_status.py
from mboxviewer.status import EmbedStatus


def test_lifecycle():
    s = EmbedStatus()
    assert s.snapshot()["state"] == "idle"
    assert s.snapshot()["ready"] is False

    s.start_chunking(message_total=10)
    s.update_chunks(messages_done=5)
    snap = s.snapshot()
    assert snap["state"] == "chunking"
    assert snap["messages_done"] == 5
    assert snap["messages_total"] == 10

    s.start_embedding(vectors_total=40)
    s.update_vectors(vectors_done=10)
    snap = s.snapshot()
    assert snap["state"] == "embedding"
    assert snap["vectors_done"] == 10
    assert snap["vectors_total"] == 40
    assert snap["ready"] is False

    s.finish()
    snap = s.snapshot()
    assert snap["state"] == "ready"
    assert snap["ready"] is True


def test_fail_does_not_stick_ready():
    s = EmbedStatus()
    s.start_chunking(1)
    s.fail("boom")
    snap = s.snapshot()
    assert snap["state"] == "error"
    assert snap["ready"] is False
    assert snap["error"] == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_embed_status.py -v`
Expected: FAIL (no `EmbedStatus`).

- [ ] **Step 3: Implement — append to `src/mboxviewer/status.py`**

```python
class EmbedStatus:
    """Thread-safe progress for the semantic-tier background passes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"   # idle | chunking | embedding | ready | error
        self._messages_done = 0
        self._messages_total = 0
        self._vectors_done = 0
        self._vectors_total = 0
        self._error = None

    def start_chunking(self, message_total):
        with self._lock:
            self._state = "chunking"
            self._messages_total = message_total
            self._messages_done = 0
            self._error = None

    def update_chunks(self, messages_done):
        with self._lock:
            self._messages_done = messages_done

    def start_embedding(self, vectors_total):
        with self._lock:
            self._state = "embedding"
            self._vectors_total = vectors_total
            self._vectors_done = 0

    def update_vectors(self, vectors_done):
        with self._lock:
            self._vectors_done = vectors_done

    def finish(self):
        with self._lock:
            self._state = "ready"
            if self._vectors_total:
                self._vectors_done = self._vectors_total

    def fail(self, error):
        with self._lock:
            self._state = "error"
            self._error = str(error)

    def snapshot(self):
        with self._lock:
            return {
                "state": self._state,
                "ready": self._state == "ready",
                "messages_done": self._messages_done,
                "messages_total": self._messages_total,
                "vectors_done": self._vectors_done,
                "vectors_total": self._vectors_total,
                "error": self._error,
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_embed_status.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/status.py tests/test_embed_status.py
git commit -m "feat(status): EmbedStatus progress holder for semantic passes"
```

---

## Task 8: `embed_index.py` — chunk + embed background passes

**Files:**
- Create: `src/mboxviewer/embed_index.py`
- Test: `tests/test_embed_index.py`

Both passes are resumable (skip when already done) and report progress. They re-use `reader`/`extract` to get body + attachment text (same helpers the indexer uses).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embed_index.py
from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.status import EmbedStatus
from mboxviewer.indexer import build_index
from mboxviewer import embed_index


class FakeEmbedder:
    model_name = "fake"
    dim = 4

    def embed_texts(self, texts):
        # deterministic, content-dependent, 4-d
        out = []
        for t in texts:
            h = sum(ord(ch) for ch in t)
            out.append([float(h % 7), float(len(t) % 5), 1.0, 0.0])
        return out


def _indexed_store(tmp_path, sample_mbox):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    build_index(settings, s)
    return settings, s


def test_chunk_then_embed(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    emb = FakeEmbedder()
    status = EmbedStatus()

    embed_index.build_chunks(settings, store, status)
    assert store.count_chunks() > 0
    # sample mbox has a PDF ("INVOICE 12345") and a DOCX ("QUARTERLY REPORT");
    # attachment text must produce attachment-kind chunks.
    kinds = {r["kind"] for r in store.conn.execute("SELECT kind FROM chunks")}
    assert "body" in kinds and "attachment" in kinds

    embed_index.build_embeddings(settings, store, emb, status)
    assert store.chunks_without_vectors(100) == []
    assert status.snapshot()["ready"] is True
    assert store.embed_meta_get() == ("fake", 4, "local")


def test_chunk_pass_is_resumable(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    embed_index.build_chunks(settings, store, EmbedStatus())
    n = store.count_chunks()
    embed_index.build_chunks(settings, store, EmbedStatus())  # re-run is a no-op
    assert store.count_chunks() == n


def test_embed_pass_resumes_after_partial(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    emb = FakeEmbedder()
    embed_index.build_chunks(settings, store, EmbedStatus())
    embed_index.build_embeddings(settings, store, emb, EmbedStatus())
    store.clear_vectors()
    store.ensure_vector_schema(emb.dim)
    assert len(store.chunks_without_vectors(100)) == store.count_chunks()
    embed_index.build_embeddings(settings, store, emb, EmbedStatus())
    assert store.chunks_without_vectors(100) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_embed_index.py -v`
Expected: FAIL (no module `embed_index`).

- [ ] **Step 3: Implement**

```python
# src/mboxviewer/embed_index.py
"""Two decoupled, resumable background passes for the semantic tier:
build_chunks (extract + store chunk text) then build_embeddings (vectorize).
Kept separate from the core indexer so its contracts are untouched."""
import sys

from .reader import read_message, iter_attachments
from .extract import extract_text
from .indexer import _body_text
from .chunker import iter_chunks

CHUNK_COMMIT_EVERY = 200
EMBED_BATCH = 64


def build_chunks(settings, store, status):
    """Walk indexed messages, re-extract body+attachment text, store chunk rows.
    Resumable: a no-op if chunks already exist."""
    if store.count_chunks() > 0:
        status.finish()  # already chunked (and presumably embedded); embed pass re-checks
        return
    messages = store.iter_messages_for_chunking()
    status.start_chunking(len(messages))
    done = 0
    for row in messages:
        mid, offset, length = row["id"], row["offset"], row["length"]
        try:
            msg = read_message(settings.mbox_path, offset, length)
            subject = msg["subject"] or ""
            from_addr = msg["from"] or ""
            date = msg["date"] or ""
            body = _body_text(msg)
            atts = []
            for idx, filename, mime, payload in iter_attachments(msg):
                atts.append((idx, filename or "", extract_text(filename, mime, payload)))
            for ch in iter_chunks(subject, from_addr, date, body, atts):
                store.add_chunk(mid, ch.kind, ch.ord, ch.source_idx, ch.text)
        except Exception as exc:  # skip a bad message; keep going
            sys.stderr.write(f"chunk pass: skipping message {mid}: {exc}\n")
        done += 1
        if done % CHUNK_COMMIT_EVERY == 0:
            store.commit()
            status.update_chunks(done)
    store.commit()
    status.update_chunks(done)


def build_embeddings(settings, store, embedder, status):
    """Vectorize chunks lacking a vector. Resumable; records embed model/dim/backend."""
    store.ensure_vector_schema(embedder.dim)
    total = store.count_chunks()
    pending = len(store.chunks_without_vectors(total or 1))
    status.start_embedding(total)
    status.update_vectors(total - pending)
    while True:
        batch = store.chunks_without_vectors(EMBED_BATCH)
        if not batch:
            break
        ids = [cid for cid, _ in batch]
        texts = [t for _, t in batch]
        vectors = embedder.embed_texts(texts)
        for cid, vec in zip(ids, vectors):
            store.add_vector(cid, vec)
        store.commit()
        status.update_vectors(status.snapshot()["vectors_done"] + len(batch))
    store.embed_meta_set(embedder.model_name, embedder.dim, settings.embed_backend)
    status.finish()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_embed_index.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/embed_index.py tests/test_embed_index.py
git commit -m "feat(embed_index): resumable chunk + embed background passes"
```

---

## Task 9: `assistant.py` — prompt assembly + Claude streaming

**Files:**
- Create: `src/mboxviewer/assistant.py`
- Test: `tests/test_assistant.py`

The Claude SDK call is isolated in `make_anthropic_generate(client, model)`, which returns a `generate(system, messages) -> iterator[str]`. The rest (`build_context_block`, `iter_answer`) is pure and tested with a fake `generate`. Citations use the integer `messages.id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assistant.py
from mboxviewer import assistant
from mboxviewer.retrieve import Snippet


def _snips():
    return [
        Snippet(7, "Roof leak", "bob@x.com", "2024-03-01", "water in the attic"),
        Snippet(9, "Invoice", "acme@x.com", "2024-03-05", "amount due $500"),
    ]


def test_context_block_labels_ids():
    block = assistant.build_context_block(_snips())
    assert "[#7]" in block and "[#9]" in block
    assert "water in the attic" in block
    assert "Roof leak" in block


def test_sources_payload():
    src = assistant.sources_for(_snips())
    assert src == [
        {"id": 7, "subject": "Roof leak", "from": "bob@x.com", "date": "2024-03-01"},
        {"id": 9, "subject": "Invoice", "from": "acme@x.com", "date": "2024-03-05"},
    ]


def test_iter_answer_sends_system_history_and_context():
    captured = {}

    def fake_generate(system, messages):
        captured["system"] = system
        captured["messages"] = messages
        yield "The roof "
        yield "leaked [#7]."

    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}]
    out = "".join(assistant.iter_answer(
        fake_generate, history, "what leaked?", _snips()))

    assert out == "The roof leaked [#7]."
    assert "only" in captured["system"].lower()           # grounding instruction
    assert captured["messages"][0] == {"role": "user", "content": "hi"}
    assert captured["messages"][1] == {"role": "assistant", "content": "hello"}
    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert "what leaked?" in last["content"]
    assert "[#7]" in last["content"]                       # context block appended


def test_make_anthropic_generate_uses_streaming():
    class FakeStream:
        text_stream = ["a", "b", "c"]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeMessages:
        def __init__(self): self.kwargs = None
        def stream(self, **kwargs):
            self.kwargs = kwargs
            return FakeStream()

    class FakeClient:
        def __init__(self): self.messages = FakeMessages()

    client = FakeClient()
    gen = assistant.make_anthropic_generate(client, "claude-sonnet-4-6")
    out = "".join(gen("SYS", [{"role": "user", "content": "q"}]))
    assert out == "abc"
    assert client.messages.kwargs["model"] == "claude-sonnet-4-6"
    assert client.messages.kwargs["system"] == "SYS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_assistant.py -v`
Expected: FAIL (no module `assistant`).

- [ ] **Step 3: Implement**

```python
# src/mboxviewer/assistant.py
"""Assistant tier: assemble a grounded prompt from retrieved snippets and stream
a cited answer from Claude. The SDK call is isolated in make_anthropic_generate so
the rest is pure and testable without a network."""
from typing import Callable, Dict, Iterator, List

from .retrieve import Snippet

SYSTEM_PROMPT = (
    "You are an assistant answering questions about the user's own email archive. "
    "Answer ONLY from the email context provided in the user's message. "
    "Cite every claim inline as [#<id>], where <id> is the integer message id shown "
    "for each snippet (e.g. [#42]); use only ids present in the context. "
    "If the answer is not in the provided email, say so plainly rather than guessing. "
    "Be concise."
)


def build_context_block(snippets: List[Snippet]) -> str:
    parts = []
    for s in snippets:
        header = " · ".join(p for p in (s.subject, s.from_addr, s.date) if p)
        parts.append(f"[#{s.message_id}] {header}\n{s.text}")
    return "\n\n".join(parts)


def sources_for(snippets: List[Snippet]) -> List[Dict]:
    return [{"id": s.message_id, "subject": s.subject,
             "from": s.from_addr, "date": s.date} for s in snippets]


def iter_answer(generate: Callable[[str, List[Dict]], Iterator[str]],
                history: List[Dict], question: str,
                snippets: List[Snippet]) -> Iterator[str]:
    """Stream the answer text. `generate(system, messages)` yields text chunks."""
    context = build_context_block(snippets)
    user_turn = (
        f"{question}\n\n"
        f"Context — email snippets (cite by the [#id] shown):\n\n{context}"
        if snippets else
        f"{question}\n\n(No matching email was found in the archive.)"
    )
    messages = list(history) + [{"role": "user", "content": user_turn}]
    for chunk in generate(SYSTEM_PROMPT, messages):
        yield chunk


def make_anthropic_generate(client, model: str):
    """Wrap an anthropic client into a generate(system, messages) -> iterator[str]."""
    def generate(system, messages):
        with client.messages.stream(
                model=model, max_tokens=1024, system=system, messages=messages) as stream:
            for text in stream.text_stream:
                yield text
    return generate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_assistant.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/assistant.py tests/test_assistant.py
git commit -m "feat(assistant): grounded prompt assembly + Claude streaming glue"
```

---

## Task 10: `api.py` — capabilities, semantic search, chat, background passes

**Files:**
- Modify: `src/mboxviewer/api.py`
- Test: `tests/test_api_assistant.py`

Wire the tiers into `create_app`: vector-aware `Store`, an embedder + `EmbedStatus`, a background thread that runs the chunk→embed passes after indexing, `/api/capabilities`, a `mode` param on `/api/search`, and `/api/assistant/chat` (NDJSON stream).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_assistant.py
import json

from fastapi.testclient import TestClient

from mboxviewer.config import Settings
from mboxviewer.api import create_app


def _app(tmp_path, sample_mbox, **kw):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"), **kw)
    return create_app(settings, index_in_background=False)


def test_capabilities_off_by_default(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    caps = c.get("/api/capabilities").json()
    assert caps["semantic"]["enabled"] is False
    assert caps["assistant"]["enabled"] is False


def test_chat_404_when_disabled(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    r = c.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


def test_search_keyword_mode_unaffected(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    r = c.get("/api/search", params={"q": "Welcome"})
    assert r.status_code == 200
    assert any(m["subject"] == "Welcome aboard" for m in r.json()["messages"])


def test_semantic_tier_builds_and_serves(tmp_path, sample_mbox, monkeypatch):
    # Force the fake embedder so no model download / network is needed.
    from mboxviewer import embed

    class FakeEmbedder:
        model_name = "fake"
        dim = 4
        def embed_texts(self, texts):
            return [[float(sum(map(ord, t)) % 7), float(len(t) % 5), 1.0, 0.0] for t in texts]

    monkeypatch.setattr(embed, "make_embedder", lambda settings: FakeEmbedder())

    app = _app(tmp_path, sample_mbox, semantic_search_enabled=True)
    c = TestClient(app)
    # background passes run synchronously when index_in_background=False -> wait via status
    caps = c.get("/api/capabilities").json()
    assert caps["semantic"]["enabled"] is True
    # hybrid search returns results (falls back to keyword if not ready)
    r = c.get("/api/search", params={"q": "report", "mode": "hybrid"})
    assert r.status_code == 200


def test_chat_streams_with_fake_client(tmp_path, sample_mbox, monkeypatch):
    from mboxviewer import embed, assistant

    class FakeEmbedder:
        model_name = "fake"; dim = 4
        def embed_texts(self, texts):
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "make_embedder", lambda settings: FakeEmbedder())
    # Replace the Anthropic generate with a deterministic fake.
    monkeypatch.setattr(assistant, "make_anthropic_generate",
                        lambda client, model: (lambda system, messages: iter(["Answer ", "[#1]"])))

    app = _app(tmp_path, sample_mbox, assistant_enabled=True, anthropic_api_key="sk-ant-test")
    c = TestClient(app)
    with c.stream("POST", "/api/assistant/chat",
                  json={"messages": [{"role": "user", "content": "what was sent?"}]}) as r:
        assert r.status_code == 200
        lines = [json.loads(ln) for ln in r.iter_lines() if ln]
    types = [d["type"] for d in lines]
    assert types[0] == "sources"
    assert "token" in types
    assert types[-1] == "done"
    assert "".join(d["text"] for d in lines if d["type"] == "token") == "Answer [#1]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api_assistant.py -v`
Expected: FAIL (no `/api/capabilities`, chat route, etc.).

- [ ] **Step 3: Implement — imports + store wiring**

In `src/mboxviewer/api.py`, add imports near the existing ones:

```python
import json
from .status import IndexStatus, EmbedStatus
from . import embed as embed_mod
from . import retrieve as retrieve_mod
from . import assistant as assistant_mod
from .embed_index import build_chunks, build_embeddings
```

Change the `Store(...)` construction in `create_app` to be vector-aware:

```python
    store = Store(settings.index_path, enable_vectors=settings.semantic_active())
```

- [ ] **Step 4: Implement — embedder, EmbedStatus, and the background passes**

After the existing `archive_*` state setup in `create_app`, add:

```python
    embed_status = EmbedStatus()
    app.state.embed_status = embed_status
    embedder = embed_mod.make_embedder(settings) if settings.semantic_active() else None
    app.state.embedder = embedder

    def _run_embed():
        try:
            build_chunks(settings, store, embed_status)
            build_embeddings(settings, store, embedder, embed_status)
        except Exception as exc:  # noqa: BLE001 - surface to the capabilities probe
            sys.stderr.write(f"semantic build failed: {exc}\n")
            embed_status.fail(exc)
        except BaseException as exc:
            embed_status.fail(RuntimeError(f"semantic build interrupted: {exc}"))
            raise
```

Then, modify the existing index-launch block so the semantic passes run **after** indexing. Replace the existing `if index_is_current(...) / elif / else` block with:

```python
    def _index_then_embed():
        _run_index()
        if settings.semantic_active():
            _run_embed()

    if index_is_current(settings, store):
        status.mark_ready(store.message_count())
        if settings.semantic_active():
            if index_in_background:
                threading.Thread(target=_run_embed, daemon=True).start()
            else:
                _run_embed()
    elif index_in_background:
        threading.Thread(target=_index_then_embed, daemon=True).start()
    else:
        _index_then_embed()
```

- [ ] **Step 5: Implement — capabilities + semantic flag helper**

Add a small helper above the routes and the `/api/capabilities` route:

```python
    def _semantic_ready():
        return settings.semantic_active() and embed_status.snapshot()["ready"]

    @app.get("/api/capabilities")
    def capabilities():
        snap = embed_status.snapshot()
        sem_on = settings.semantic_active()
        asst_on = settings.assistant_active()
        return {
            "semantic": {
                "enabled": sem_on,
                "ready": sem_on and snap["ready"],
                "state": snap["state"] if sem_on else "off",
                "messages_done": snap["messages_done"],
                "messages_total": snap["messages_total"],
                "vectors_done": snap["vectors_done"],
                "vectors_total": snap["vectors_total"],
            },
            "assistant": {
                "enabled": asst_on,
                "ready": asst_on and snap["ready"],
                "model": settings.gen_model if asst_on else None,
            },
        }
```

- [ ] **Step 6: Implement — `mode` param on `/api/search`**

Replace the existing `search` route body with a `mode`-aware version (keyword default; hybrid only when ready, else fall back to keyword):

```python
    @app.get("/api/search")
    def search(q: str = Query(...), label: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               from_q: Optional[str] = None, has_attachment: bool = False,
               sort: str = "date_desc", mode: str = "keyword"):
        offset = (page - 1) * page_size
        if mode == "hybrid" and _semantic_ready() and not label and page == 1 \
                and not (date_from or date_to or from_q or has_attachment):
            rows = retrieve_mod.search(store, embedder, q, page_size)
            return {"messages": [_msg_summary(r) for r in rows], "page": page,
                    "mode": "hybrid"}
        rows = store.search(q, label, page_size, offset,
                            date_from=date_from, date_to=date_to, from_q=from_q,
                            has_attachment=has_attachment, sort=sort)
        return {"messages": [_msg_summary(r) for r in rows], "page": page,
                "mode": "keyword"}
```

(Hybrid mode is page-1 + no-filter only in Phase 1; with filters/labels/pagination it falls back to keyword. Noted in the spec's "advanced query UI" out-of-scope.)

- [ ] **Step 7: Implement — `/api/assistant/chat`**

Add the chat route (place it after `/api/search`):

```python
    @app.post("/api/assistant/chat")
    def assistant_chat(payload: dict):
        if not settings.assistant_active():
            raise HTTPException(404, "assistant not enabled")
        messages = payload.get("messages") or []
        if not messages or messages[-1].get("role") != "user":
            raise HTTPException(400, "last message must be a user turn")
        question = messages[-1]["content"]
        history = messages[:-1]

        def _ndjson():
            snap = embed_status.snapshot()
            if not snap["ready"]:
                total = snap["vectors_total"] or 1
                pct = round(snap["vectors_done"] / total * 100, 1)
                yield json.dumps({"type": "building", "percent": pct}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"
                return
            snippets = retrieve_mod.retrieve_context(store, embedder, question)
            yield json.dumps({"type": "sources",
                              "sources": assistant_mod.sources_for(snippets)}) + "\n"
            client = anthropic_client()
            generate = assistant_mod.make_anthropic_generate(client, settings.gen_model)
            try:
                for text in assistant_mod.iter_answer(generate, history, question, snippets):
                    yield json.dumps({"type": "token", "text": text}) + "\n"
            except Exception as exc:  # noqa: BLE001
                yield json.dumps({"type": "error", "error": str(exc)}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"

        return StreamingResponse(_ndjson(), media_type="application/x-ndjson")
```

Add the lazy Anthropic client factory near the top of `create_app` (after `embedder` setup):

```python
    _client_box = {}

    def anthropic_client():
        if "c" not in _client_box:
            import anthropic  # lazy
            _client_box["c"] = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return _client_box["c"]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_assistant.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Confirm the whole suite is green**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add src/mboxviewer/api.py tests/test_api_assistant.py
git commit -m "feat(api): capabilities, semantic search mode, chat stream, build passes"
```

---

## Task 11: Frontend — Ask tab, semantic toggle, chat UI

**Files:**
- Modify: `src/mboxviewer/static/index.html`
- Modify: `src/mboxviewer/static/app.js`
- Modify: `src/mboxviewer/static/style.css`

No pytest here; verification is in the browser (Step 5). Keep all email-controlled strings `escapeHtml`'d.

- [ ] **Step 1: `index.html` — add the Ask tab, semantic toggle, and chat pane**

In `#header-actions`, add the Ask tab button after the Files tab:

```html
      <button id="tab-ask" type="button" class="tab" title="Ask questions about your mail" hidden>Ask</button>
```

In `#searchbar`, add a semantic toggle button right after the `#q` input:

```html
        <button id="search-mode" type="button" title="Toggle semantic search" hidden>Keyword</button>
```

Add a chat section inside `#app`, after the `#reader` section:

```html
    <section id="chat" hidden>
      <div id="chat-log"></div>
      <div id="chat-building" hidden></div>
      <form id="chat-form">
        <input id="chat-input" type="text" placeholder="Ask about your mail…" autocomplete="off">
        <button type="submit">Send</button>
      </form>
    </section>
```

- [ ] **Step 2: `app.js` — capabilities probe + tab/toggle wiring**

Add near the top of `app.js` (after existing element lookups):

```javascript
// --- Assistant / semantic tiers ---
let caps = { semantic: { enabled: false, ready: false }, assistant: { enabled: false } };
let searchMode = "keyword";          // "keyword" | "hybrid"
const chatHistory = [];              // session-only conversation

async function loadCapabilities() {
  try {
    caps = await (await fetch("/api/capabilities")).json();
  } catch (e) { return; }
  const askTab = document.getElementById("tab-ask");
  const modeBtn = document.getElementById("search-mode");
  if (caps.assistant.enabled) askTab.hidden = false;
  if (caps.semantic.enabled) modeBtn.hidden = false;
}

function refreshSemanticState() {
  // Poll capabilities while the knowledge base builds; update hints.
  if (!caps.semantic.enabled) return;
  fetch("/api/capabilities").then(r => r.json()).then(c => {
    caps = c;
    const modeBtn = document.getElementById("search-mode");
    if (!caps.semantic.ready) {
      const v = caps.semantic.vectors_total
        ? Math.round(caps.semantic.vectors_done / caps.semantic.vectors_total * 100) : 0;
      modeBtn.title = `Building knowledge base… ${v}%`;
    } else {
      modeBtn.title = "Toggle semantic search";
    }
    const building = document.getElementById("chat-building");
    if (building) {
      building.hidden = caps.semantic.ready;
      if (!caps.semantic.ready) {
        const v = caps.semantic.vectors_total
          ? Math.round(caps.semantic.vectors_done / caps.semantic.vectors_total * 100) : 0;
        building.textContent = `Building knowledge base… ${v}%`;
      }
    }
    if (!caps.semantic.ready) setTimeout(refreshSemanticState, 3000);
  });
}

document.getElementById("search-mode").addEventListener("click", () => {
  searchMode = searchMode === "keyword" ? "hybrid" : "keyword";
  document.getElementById("search-mode").textContent =
    searchMode === "hybrid" ? "Semantic" : "Keyword";
  reload();   // existing refresh fn (app.js:193) re-runs the current query
});
```

- [ ] **Step 3: `app.js` — pass `mode` into the search request (`pageUrl`) and wire the Ask tab**

In the existing `pageUrl(page)` function (app.js:113), the search branch is the line:

```javascript
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
```

Replace it with a mode-aware version:

```javascript
  if (currentQuery) {
    params.set("q", currentQuery);
    if (searchMode === "hybrid" && caps.semantic && caps.semantic.ready) {
      params.set("mode", "hybrid");
    }
    return `/api/search?${params.toString()}`;
  }
```

Add a helper that leaves the chat view (restores the normal 3-pane layout), then wire the Ask tab. The existing tab elements are `tabFolders` / `tabFiles` (already defined in app.js) and switching uses `setMode(...)`:

```javascript
function exitChat() {
  document.getElementById("chat").hidden = true;
  document.getElementById("labels").hidden = false;
  document.getElementById("list").hidden = false;
  document.getElementById("reader").hidden = false;
  document.getElementById("tab-ask").classList.remove("active");
}

document.getElementById("tab-ask").addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.getElementById("tab-ask").classList.add("active");
  document.getElementById("labels").hidden = true;
  document.getElementById("list").hidden = true;
  document.getElementById("reader").hidden = true;
  document.getElementById("chat").hidden = false;
  refreshSemanticState();
  document.getElementById("chat-input").focus();
});
```

Then add `exitChat();` as the first line inside the existing `tabFolders` and `tabFiles` click handlers (app.js:548 and :551) so switching back to Folders/Files restores the normal layout:

```javascript
tabFolders.addEventListener("click", () => {
  exitChat();   // <-- add this line
  if (browseMode !== "folders") setMode("folders"); else toggleCollapse();
});
tabFiles.addEventListener("click", () => {
  exitChat();   // <-- add this line
  if (browseMode !== "files") setMode("files"); else toggleCollapse();
});
```

- [ ] **Step 4: `app.js` — chat send + NDJSON streaming**

```javascript
function appendBubble(role, html) {
  const log = document.getElementById("chat-log");
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.innerHTML = html;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function renderAnswer(text, sources) {
  // Turn [#id] markers into clickable citation chips; escape everything else.
  const byId = {};
  (sources || []).forEach(s => { byId[s.id] = s; });
  let out = "";
  let last = 0;
  const re = /\[#(\d+)\]/g, m = [];
  let match;
  while ((match = re.exec(text)) !== null) {
    out += escapeHtml(text.slice(last, match.index));
    const id = match[1];
    out += `<a href="#" class="cite" data-id="${id}">#${id}</a>`;
    last = re.lastIndex;
  }
  out += escapeHtml(text.slice(last));
  return out;
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  appendBubble("user", escapeHtml(q));
  chatHistory.push({ role: "user", content: q });
  const answerDiv = appendBubble("assistant", "…");
  let sources = [], text = "";

  const resp = await fetch("/api/assistant/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages: chatHistory }),
  });
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
      if (!line.trim()) continue;
      const ev = JSON.parse(line);
      if (ev.type === "building") {
        answerDiv.innerHTML = `Building knowledge base… ${ev.percent}%. Try again shortly.`;
      } else if (ev.type === "sources") {
        sources = ev.sources;
      } else if (ev.type === "token") {
        text += ev.text;
        answerDiv.innerHTML = renderAnswer(text, sources);
      } else if (ev.type === "error") {
        answerDiv.innerHTML = `<span class="err">${escapeHtml(ev.error)}</span>`;
      }
    }
  }
  if (text) chatHistory.push({ role: "assistant", content: text });
  // citation chips open the message in the reader pane (reuse existing openMessage)
  answerDiv.querySelectorAll("a.cite").forEach(a => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      openMessage(parseInt(a.dataset.id, 10));   // existing message-open fn
    });
  });
});
```

(If the existing message-open function has a different name than `openMessage`, use that name. Confirm by reading `app.js`.)

Call `loadCapabilities()` once during the existing startup/init path.

- [ ] **Step 5: `style.css` — minimal chat + toggle styles**

```css
#chat { display: flex; flex-direction: column; height: 100%; padding: 1rem; overflow: hidden; }
#chat-log { flex: 1; overflow-y: auto; }
#chat-building { padding: .5rem; color: #888; font-style: italic; }
.bubble { margin: .4rem 0; padding: .5rem .75rem; border-radius: 8px; max-width: 80%; white-space: pre-wrap; }
.bubble.user { background: #e8f0fe; margin-left: auto; }
.bubble.assistant { background: #f4f4f4; }
.bubble .cite { font-size: .85em; text-decoration: none; background: #ddd; border-radius: 4px; padding: 0 4px; margin: 0 1px; }
.bubble .err { color: #b00; }
#chat-form { display: flex; gap: .5rem; padding-top: .5rem; }
#chat-input { flex: 1; }
#search-mode { white-space: nowrap; }
```

- [ ] **Step 6: Manual browser verification (semantic + assistant)**

Run locally with the assistant on (uses the real fastembed model — first run downloads it; set a real key):

```bash
PYTHONPATH=src MBOX_PATH=/path/to/sample.mbox INDEX_PATH=/tmp/i.db \
  SEMANTIC_SEARCH=1 ASSISTANT_ENABLED=1 ANTHROPIC_API_KEY=sk-ant-... \
  .venv/bin/python -m mboxviewer.main
```

Verify: the "Ask" tab and "Keyword/Semantic" toggle appear; the toggle shows a building hint, then switches to semantic ranking; the Ask tab streams an answer with clickable `[#id]` chips that open the cited message. Then verify with **both env vars unset** that the tab and toggle are hidden and the app behaves exactly as before.

- [ ] **Step 7: Commit**

```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat(ui): Ask tab + semantic search toggle + streaming cited chat"
```

---

## Task 12: Ops + docs — env passthrough and README

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: `docker-compose.yml` — pass the new env vars**

Add to the `environment:` list of the `mbox-viewer` service:

```yaml
      - SEMANTIC_SEARCH=${SEMANTIC_SEARCH:-}
      - ASSISTANT_ENABLED=${ASSISTANT_ENABLED:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - ASSISTANT_MODEL=${ASSISTANT_MODEL:-claude-sonnet-4-6}
      - EMBED_BACKEND=${EMBED_BACKEND:-local}
      - EMBED_MODEL=${EMBED_MODEL:-BAAI/bge-small-en-v1.5}
      - OLLAMA_URL=${OLLAMA_URL:-http://host.docker.internal:11434}
```

- [ ] **Step 2: `.env.example` — document the toggles**

Append:

```bash
# --- Optional: local semantic search (fully on-device; no API key, no egress) ---
# Set to 1 to build a local vector index and enable meaning-based search.
SEMANTIC_SEARCH=

# --- Optional: AI assistant (chat). Requires an Anthropic API key. ---
# When enabled, ONLY retrieved email snippets are sent to Anthropic (never the whole
# mailbox, never the embeddings). Implies SEMANTIC_SEARCH.
ASSISTANT_ENABLED=
ANTHROPIC_API_KEY=
ASSISTANT_MODEL=claude-sonnet-4-6

# Embedding backend: "local" (in-container CPU, default) or "ollama" (host, Metal).
EMBED_BACKEND=local
# EMBED_MODEL=BAAI/bge-small-en-v1.5
# OLLAMA_URL=http://host.docker.internal:11434
```

- [ ] **Step 3: `README.md` — add an "AI features (optional)" section**

Add a section documenting both tiers, mirroring the spec's §2 table and §13 security:
- Semantic search (`SEMANTIC_SEARCH=1`): fully local, no key, no egress; first enable runs a one-time background build (CPU; downloads a ~130 MB embedding model on first use).
- Assistant (`ASSISTANT_ENABLED=1` + `ANTHROPIC_API_KEY`): multi-turn cited chat; only retrieved snippets leave the machine; configurable model (`ASSISTANT_MODEL`, default `claude-sonnet-4-6`); cost note (~1–5¢/question).
- Reiterate the existing localhost-only / no-auth caution applies, more so with these on.

- [ ] **Step 4: Verify compose config parses**

Run: `docker compose config >/dev/null && echo ok`
Expected: `ok` (no YAML/interpolation errors).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example README.md
git commit -m "docs+ops: env passthrough and README for the two AI tiers"
```

---

## Final verification (after all tasks)

- [ ] **Full suite green:** `.venv/bin/pytest -q`
- [ ] **Default deployment unchanged:** run with both env vars unset; confirm no Ask tab, no semantic toggle, no background passes, identical behavior.
- [ ] **Real end-to-end (optional, costs API tokens):** run with `SEMANTIC_SEARCH=1 ASSISTANT_ENABLED=1 ANTHROPIC_API_KEY=…` against the real `sample.mbox`; confirm semantic search ranks by meaning and the assistant streams a cited answer that opens the right message.
- [ ] **Docker build:** `docker compose build` succeeds with the new deps.

---

## Spec coverage check

| Spec section | Task(s) |
|---|---|
| §2 two-tier model + activation rules | 1, 10 |
| §6 config | 1 |
| §7 data model (chunks, vec_chunks, store methods) | 4 |
| §8 background passes (chunk + embed, resumable) | 7, 8 |
| §9 modules (embed, chunker, retrieve, assistant) | 3, 5, 6, 9 |
| §10 generation contract (citations, streaming) | 9 |
| §11 API (capabilities, search mode, chat) | 10 |
| §12 frontend (toggle, Ask tab, chips) | 11 |
| §13 security (lazy deps, key handling) | 5, 9, 10, 12 |
| §14 dependencies | 2 |
| §15 testing | every task (TDD) |
| O1 (no SCHEMA_VERSION bump) | 4 |
| O2 (lazy model download) | 5, 12 |
| O3 (cap/window defaults) | 3 |
