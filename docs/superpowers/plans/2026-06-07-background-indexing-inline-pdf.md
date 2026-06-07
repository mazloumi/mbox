# Background Indexing + Status, and Inline PDF — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the viewer serve immediately and index on a background thread with on-site progress (`/api/status` + a status bar that fills in live), and let PDF attachments render inline in the reader pane.

**Architecture:** A thread-safe `IndexStatus` holder is updated by a daemon indexer thread and read by `GET /api/status`. `Store` switches to thread-local SQLite connections so the background writer and request-thread readers never share a connection (WAL makes concurrent read+write safe). The attachment endpoint gains an `inline` mode; the frontend adds a status bar and an inline PDF `<iframe>`.

**Tech Stack:** Python 3.9-compatible (Docker uses 3.12), FastAPI, SQLite WAL, `threading`, vanilla JS. Tests: pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-06-07-background-indexing-inline-pdf-design.md`

---

## File Structure

```
src/mboxviewer/
  status.py      # NEW — IndexStatus thread-safe progress holder
  store.py       # MODIFY — thread-local connections + message_count()
  indexer.py     # MODIFY — progress(count, bytes_done) + PROGRESS_EVERY
  api.py         # MODIFY — background index thread, /api/status, inline disposition, create_app(index_in_background)
  static/
    index.html   # MODIFY — status bar element + inline PDF iframe
    app.js        # MODIFY — status polling + viewPdf()
    style.css     # MODIFY — status bar, pdf iframe, flex layout
tests/
  test_status.py # NEW
  test_store.py  # MODIFY — thread-local read test
  test_indexer.py# MODIFY — 2-arg progress test
  test_api.py    # MODIFY — status + inline tests; client fixture uses index_in_background=False
```

Each unit stays single-purpose: `status.py` only holds progress state; `store.py` stays the only SQLite access point; `api.py` wires threads + routes.

---

## Task 1: IndexStatus holder

**Files:**
- Create: `src/mboxviewer/status.py`
- Test: `tests/test_status.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_status.py`:
```python
from mboxviewer.status import IndexStatus


def test_new_status_is_idle():
    s = IndexStatus().snapshot()
    assert s["indexing"] is False and s["ready"] is False
    assert s["messages"] == 0 and s["percent"] == 0.0 and s["error"] is None


def test_start_and_update_progress():
    st = IndexStatus()
    st.start(1000)
    st.update(250, 400)
    s = st.snapshot()
    assert s["indexing"] is True and s["ready"] is False
    assert s["messages"] == 250 and s["bytes_done"] == 400
    assert s["bytes_total"] == 1000 and s["percent"] == 40.0


def test_finish_sets_ready_and_full():
    st = IndexStatus()
    st.start(1000)
    st.update(250, 400)
    st.finish()
    s = st.snapshot()
    assert s["indexing"] is False and s["ready"] is True
    assert s["bytes_done"] == 1000 and s["percent"] == 100.0


def test_fail_records_error():
    st = IndexStatus()
    st.start(1000)
    st.fail(RuntimeError("boom"))
    s = st.snapshot()
    assert s["indexing"] is False and s["ready"] is False
    assert "boom" in s["error"]


def test_mark_ready_for_reused_index():
    st = IndexStatus()
    st.mark_ready(messages=42)
    s = st.snapshot()
    assert s["ready"] is True and s["indexing"] is False
    assert s["messages"] == 42 and s["percent"] == 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.status'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/status.py`:
```python
import threading


class IndexStatus:
    """Thread-safe holder for indexing progress, read by GET /api/status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._indexing = False
        self._ready = False
        self._messages = 0
        self._bytes_done = 0
        self._bytes_total = 0
        self._error = None

    def start(self, bytes_total):
        with self._lock:
            self._indexing = True
            self._ready = False
            self._messages = 0
            self._bytes_done = 0
            self._bytes_total = bytes_total
            self._error = None

    def update(self, messages, bytes_done):
        with self._lock:
            self._messages = messages
            self._bytes_done = bytes_done

    def finish(self):
        with self._lock:
            self._indexing = False
            self._ready = True
            if self._bytes_total:
                self._bytes_done = self._bytes_total

    def fail(self, error):
        with self._lock:
            self._indexing = False
            self._ready = False
            self._error = str(error)

    def mark_ready(self, messages=0):
        with self._lock:
            self._indexing = False
            self._ready = True
            self._messages = messages

    def snapshot(self):
        with self._lock:
            total = self._bytes_total
            done = self._bytes_done
            if total:
                percent = round(done / total * 100, 1)
            else:
                percent = 100.0 if self._ready else 0.0
            return {
                "indexing": self._indexing,
                "ready": self._ready,
                "messages": self._messages,
                "bytes_done": done,
                "bytes_total": total,
                "percent": percent,
                "error": self._error,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_status.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/status.py tests/test_status.py
git commit -m "feat: thread-safe IndexStatus progress holder"
```

---

## Task 2: Store thread-local connections + message_count

**Files:**
- Modify: `src/mboxviewer/store.py` (the `__init__` and add `conn` property + `message_count`)
- Test: `tests/test_store.py` (add one test)

Background: a single `sqlite3.Connection` must not be shared across threads. Making
`Store.conn` a thread-local property means each thread (request worker, indexer
thread) lazily gets its own connection — no shared state — while all existing methods
keep calling `self.conn` unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_store.py`:
```python
import threading


def test_reads_work_from_another_thread(tmp_path):
    s = Store(str(tmp_path / "i.db"))
    s.create_schema()
    mid = s.add_message(0, 10, "<a>", "Hi", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    s.commit()
    out = {}

    def reader():
        out["rows"] = s.list_messages(None, 10, 0)
        out["count"] = s.message_count()

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert len(out["rows"]) == 1 and out["rows"][0]["id"] == mid
    assert out["count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py::test_reads_work_from_another_thread -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'message_count'`

- [ ] **Step 3: Replace `Store.__init__` and add the `conn` property + `message_count`**

In `src/mboxviewer/store.py`, change the imports at the top to include `threading`:
```python
import os
import sqlite3
import threading
from contextlib import contextmanager
```

Replace the existing `__init__` method:
```python
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
```
with:
```python
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()

    @property
    def conn(self):
        """A SQLite connection unique to the calling thread (lazily opened)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
        return c

    def message_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
```
(Leave every other method unchanged — they all use `self.conn`, which now resolves
to the calling thread's connection.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: PASS (all existing store tests + the new thread test)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/store.py tests/test_store.py
git commit -m "feat: thread-local SQLite connections and message_count in Store"
```

---

## Task 3: Indexer progress callback (count, bytes_done)

**Files:**
- Modify: `src/mboxviewer/indexer.py` (`build_index` progress call; add `PROGRESS_EVERY`)
- Test: `tests/test_indexer.py` (add one test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`:
```python
def test_progress_callback_receives_count_and_bytes(tmp_path, sample_mbox, monkeypatch):
    import mboxviewer.indexer as idx
    monkeypatch.setattr(idx, "PROGRESS_EVERY", 1)  # fire on every message
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    calls = []
    idx.build_index(settings, store, progress=lambda c, b: calls.append((c, b)))
    assert len(calls) >= 1
    last_count, last_bytes = calls[-1]
    assert last_count >= 1 and last_bytes > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_indexer.py::test_progress_callback_receives_count_and_bytes -v`
Expected: FAIL — either `AttributeError: ... PROGRESS_EVERY` or a `TypeError` (old callback takes 1 arg)

- [ ] **Step 3: Update `build_index` in `src/mboxviewer/indexer.py`**

Add the module constant next to `COMMIT_EVERY` (near the top of the file):
```python
COMMIT_EVERY = 2000
PROGRESS_EVERY = 500
```

In `build_index`, replace the per-iteration tail. The current loop body ends with:
```python
            count += 1
        except Exception as exc:
            sys.stderr.write(f"skipping message at offset {offset}: {exc}\n")
            continue
        if count % COMMIT_EVERY == 0:
            store.commit()
        if progress and count % 500 == 0:
            progress(count)
```
Replace it with (note `bytes_done` computed from the current span, and the 2-arg call):
```python
            count += 1
        except Exception as exc:
            sys.stderr.write(f"skipping message at offset {offset}: {exc}\n")
            continue
        if count % COMMIT_EVERY == 0:
            store.commit()
        if progress and count % PROGRESS_EVERY == 0:
            progress(count, offset + length)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_indexer.py -v`
Expected: PASS (all existing indexer tests + the new one)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/indexer.py tests/test_indexer.py
git commit -m "feat: indexer progress reports (count, bytes_done)"
```

---

## Task 4: API — background indexing, /api/status, inline disposition

**Files:**
- Modify: `src/mboxviewer/api.py` (full replacement below)
- Test: `tests/test_api.py` (update `client` fixture; add status + inline tests)

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, change the `client` fixture so indexing runs synchronously
(deterministic) and import the helper:
```python
from mboxviewer.api import create_app, _render_body, _content_disposition


@pytest.fixture
def client(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    return TestClient(create_app(settings, index_in_background=False))
```

Then add these tests:
```python
import time


def test_status_ready_after_sync_index(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    c = TestClient(create_app(settings, index_in_background=False))
    s = c.get("/api/status").json()
    assert s["ready"] is True and s["indexing"] is False
    assert s["messages"] == 2 and s["percent"] == 100.0 and s["error"] is None


def test_status_background_eventually_ready(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    c = TestClient(create_app(settings))  # background (default)
    s = {}
    for _ in range(100):
        s = c.get("/api/status").json()
        if s["ready"]:
            break
        time.sleep(0.05)
    assert s["ready"] is True and s["messages"] == 2


def test_status_ready_on_reused_index(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    TestClient(create_app(settings, index_in_background=False))  # build once
    c = TestClient(create_app(settings, index_in_background=False))  # reuse
    s = c.get("/api/status").json()
    assert s["ready"] is True and s["messages"] == 2


def test_attachment_inline_disposition(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    r = client.get(f"/api/messages/{mid}/attachments/0", params={"inline": "true"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-type"] == "application/pdf"


def test_attachment_default_disposition(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    r = client.get(f"/api/messages/{mid}/attachments/0")
    assert r.headers["content-disposition"].startswith("attachment")


def test_content_disposition_inline_flag():
    assert _content_disposition("a.pdf", inline=True).startswith('inline; filename="a.pdf"')
    assert _content_disposition("a.pdf").startswith("attachment;")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL (e.g. `TypeError: create_app() got an unexpected keyword argument 'index_in_background'`)

- [ ] **Step 3: Replace `src/mboxviewer/api.py` entirely**

```python
import html
import os
import re
import threading
import urllib.parse
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current
from .status import IndexStatus

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _msg_summary(row):
    return {
        "id": row["id"], "subject": row["subject"], "from": row["from_addr"],
        "to": row["to_addr"], "date": row["date"],
    }


def _render_body(mime, content, allow_remote=False):
    """HTML parts are sanitized; plain text is escaped and wrapped in <pre>."""
    if mime == "text/html":
        return sanitize_html(content, allow_remote=allow_remote)
    return "<pre>" + html.escape(content) + "</pre>"


def _content_disposition(filename, inline=False):
    """Safe Content-Disposition value; RFC 5987 for non-ASCII names."""
    filename = _CONTROL_CHARS.sub("", filename or "") or "attachment"
    kind = "inline" if inline else "attachment"
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        encoded = urllib.parse.quote(filename.encode("utf-8"))
        return f"{kind}; filename*=UTF-8''{encoded}"
    safe = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'{kind}; filename="{safe}"'


def create_app(settings, index_in_background=True):
    app = FastAPI(title="mbox viewer")
    store = Store(settings.index_path)
    store.create_schema()
    status = IndexStatus()
    app.state.store = store
    app.state.settings = settings
    app.state.status = status

    def _run_index():
        try:
            bytes_total = os.path.getsize(settings.mbox_path)
            status.start(bytes_total)
            n = build_index(settings, store, progress=status.update)
            status.update(n, bytes_total)
            status.finish()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            status.fail(exc)

    if index_is_current(settings, store):
        status.mark_ready(store.message_count())
    elif index_in_background:
        threading.Thread(target=_run_index, daemon=True).start()
    else:
        _run_index()

    @app.get("/api/status")
    def get_status():
        return status.snapshot()

    @app.get("/api/labels")
    def labels():
        return [{"name": n, "count": c} for n, c in store.list_labels()]

    @app.get("/api/messages")
    def messages(label: Optional[str] = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        offset = (page - 1) * page_size
        rows = store.list_messages(label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/search")
    def search(q: str = Query(...), label: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        offset = (page - 1) * page_size
        rows = store.search(q, label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/messages/{message_id}")
    def message_detail(message_id: int, allow_remote: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        mime, content = get_display_body(msg)
        body_html = _render_body(mime, content, allow_remote=allow_remote)
        atts = [{"idx": a["idx"], "filename": a["filename"], "mime": a["mime"], "size": a["size"]}
                for a in store.get_attachments(message_id)]
        return {**_msg_summary(row), "body_html": body_html, "attachments": atts}

    @app.get("/api/messages/{message_id}/attachments/{idx}")
    def attachment(message_id: int, idx: int, inline: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return Response(
                    content=payload, media_type=mime,
                    headers={"Content-Disposition": _content_disposition(filename, inline=inline)})
        raise HTTPException(404, "attachment not found")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS (all existing api tests with the synchronous fixture + the new status & inline tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: background indexing, /api/status, and inline attachment disposition"
```

---

## Task 5: Frontend — status bar + inline PDF

**Files:**
- Modify: `src/mboxviewer/static/index.html`
- Modify: `src/mboxviewer/static/app.js` (full replacement)
- Modify: `src/mboxviewer/static/style.css`

No unit tests (static assets); verified in Task 6.

- [ ] **Step 1: Replace `src/mboxviewer/static/index.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>mbox viewer</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="status-bar" hidden></div>
  <div id="app">
    <aside id="labels"><h2>Folders</h2><ul id="label-list"></ul></aside>
    <section id="list">
      <div id="searchbar">
        <input id="q" type="search" placeholder="Search mail and attachments...">
      </div>
      <ul id="message-list"></ul>
    </section>
    <section id="reader">
      <div id="reader-header"></div>
      <div id="reader-attachments"></div>
      <iframe id="reader-body" sandbox=""></iframe>
      <iframe id="reader-pdf" hidden></iframe>
    </section>
  </div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Replace `src/mboxviewer/static/app.js`**

```javascript
const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const readerPdf = document.getElementById("reader-pdf");
const statusBar = document.getElementById("status-bar");
const q = document.getElementById("q");

const PAGE_SIZE = 50;
let activeLabel = null;
let currentQuery = "";
let currentPage = 1;
let currentOpenId = null;

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function loadLabels() {
  try {
    const labels = await getJSON("/api/labels");
    labelList.innerHTML = "";
    for (const l of labels) {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(l.name)}<span class="count">${escapeHtml(String(l.count))}</span>`;
      li.onclick = () => { activeLabel = l.name; setActive(labelList, li); reload(); };
      labelList.appendChild(li);
    }
  } catch (err) {
    labelList.innerHTML = `<li>Failed to load folders: ${escapeHtml(String(err.message))}</li>`;
  }
}

function setActive(container, el) {
  container.querySelectorAll("li").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
}

function pageUrl(page) {
  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (activeLabel) params.set("label", activeLabel);
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
  return `/api/messages?${params.toString()}`;
}

function appendMessages(messages) {
  for (const m of messages) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">${escapeHtml(m.from || "")} — ${escapeHtml((m.date || "").slice(0, 10))}</div>`;
    li.onclick = () => { setActive(messageList, li); openMessage(m.id); };
    messageList.appendChild(li);
  }
  renderLoadMore(messages.length);
}

function renderLoadMore(lastCount) {
  const existing = document.getElementById("load-more");
  if (existing) existing.remove();
  if (lastCount === PAGE_SIZE) {
    const li = document.createElement("li");
    li.id = "load-more";
    li.textContent = "Load more…";
    li.onclick = loadNextPage;
    messageList.appendChild(li);
  }
}

async function reload() {
  currentPage = 1;
  messageList.innerHTML = "";
  try {
    const data = await getJSON(pageUrl(1));
    appendMessages(data.messages);
  } catch (err) {
    messageList.innerHTML = `<li>Failed to load messages: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function loadNextPage() {
  currentPage += 1;
  try {
    const data = await getJSON(pageUrl(currentPage));
    appendMessages(data.messages);
  } catch (err) {
    renderLoadMore(0);
  }
}

function viewPdf(id, idx) {
  readerPdf.src = `/api/messages/${id}/attachments/${idx}?inline=1`;
  readerPdf.hidden = false;
}

async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  try {
    const m = await getJSON(`/api/messages/${id}?allow_remote=${allowRemote}`);
    const remoteBtn = allowRemote ? "" : `<button id="load-remote" type="button">Load remote images</button>`;
    readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>
      ${remoteBtn}`;
    readerAtt.innerHTML = (m.attachments || []).map(a => {
      const dl = `<a href="/api/messages/${id}/attachments/${a.idx}" download>${escapeHtml(a.filename)} (${escapeHtml(String(a.size))}b)</a>`;
      const view = a.mime === "application/pdf"
        ? ` <button type="button" class="view-pdf" onclick="viewPdf(${id}, ${a.idx})">View</button>` : "";
      return `<span class="att">${dl}${view}</span>`;
    }).join("");
    readerBody.srcdoc = m.body_html;
    const btn = document.getElementById("load-remote");
    if (btn) btn.onclick = () => openMessage(id, true);
  } catch (err) {
    readerHeader.innerHTML = `<div class="meta">Failed to open message: ${escapeHtml(String(err.message))}</div>`;
    readerAtt.innerHTML = "";
    readerBody.srcdoc = "";
  }
}

async function pollStatus() {
  try {
    const s = await getJSON("/api/status");
    if (s.error) {
      statusBar.hidden = false;
      statusBar.className = "error";
      statusBar.textContent = "Indexing failed: " + s.error;
      return;
    }
    if (s.indexing) {
      statusBar.hidden = false;
      statusBar.className = "";
      statusBar.textContent = `Indexing… ${s.percent}% · ${Number(s.messages).toLocaleString()} messages`;
      loadLabels();
      if (currentOpenId === null) reload();
      setTimeout(pollStatus, 2000);
    } else {
      statusBar.hidden = true;
      loadLabels();
      if (currentOpenId === null) reload();
    }
  } catch (err) {
    statusBar.hidden = false;
    statusBar.className = "error";
    statusBar.textContent = "Status unavailable: " + err.message;
    setTimeout(pollStatus, 3000);
  }
}

let searchTimer;
q.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentQuery = q.value.trim(); reload(); }, 250);
});

loadLabels();
reload();
pollStatus();
```

- [ ] **Step 3: Update `src/mboxviewer/static/style.css`**

Replace the `body` and `#app` rules:
```css
body { margin: 0; font-family: system-ui, sans-serif; }
#app { display: grid; grid-template-columns: 220px 360px 1fr; height: 100vh; }
```
with:
```css
body { margin: 0; font-family: system-ui, sans-serif; display: flex; flex-direction: column; height: 100vh; }
#app { display: grid; grid-template-columns: 220px 360px 1fr; flex: 1; min-height: 0; }
```

Then append these rules to the end of the file:
```css
#status-bar { padding: 6px 12px; background: #fff8e1; border-bottom: 1px solid #f0e0a0;
  font-size: 13px; color: #7a6a2a; }
#status-bar.error { background: #fdecea; color: #b00020; border-color: #f5c6cb; }
#reader-pdf { flex: 1; border: 0; width: 100%; border-top: 1px solid #ddd; }
.att { display: inline-block; margin-right: 10px; }
.view-pdf { margin-left: 4px; font-size: 12px; cursor: pointer; }
#load-remote { margin-top: 8px; font-size: 12px; padding: 3px 8px; cursor: pointer; }
```
(Note: a `#load-remote` rule may already exist from the prior frontend work — if so,
leave the existing one and do not duplicate it.)

- [ ] **Step 4: Confirm the backend suite is still green (JS/CSS-only change)**

Run: `.venv/bin/pytest -q`
Expected: all green (unchanged from Task 4)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: indexing status bar and inline PDF preview in the frontend"
```

---

## Task 6: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Verify inline PDF + status via a small local run (no Docker rebuild)**

Generate a tiny mbox with a PDF attachment and run the dev server on a spare port:
```bash
.venv/bin/python - <<'PY'
import io
from email.message import EmailMessage
from email.generator import BytesGenerator
from reportlab.pdfgen import canvas
buf=io.BytesIO(); c=canvas.Canvas(buf); c.drawString(72,720,"HELLO PDF 999"); c.save(); pdf=buf.getvalue()
m=EmailMessage(); m["Subject"]="Has PDF"; m["From"]="a@x.com"; m["To"]="b@x.com"
m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
m.set_content("plain"); m.add_alternative("<p>see pdf</p>", subtype="html")
m.add_attachment(pdf, maintype="application", subtype="pdf", filename="doc.pdf")
b=io.BytesIO(); BytesGenerator(b).flatten(m); data=b.getvalue()
open("/tmp/e2e.mbox","wb").write(b"From - x\n"+data+(b"" if data.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/e2e.mbox")
PY
rm -f /tmp/e2e.db*
PYTHONPATH=src MBOX_PATH=/tmp/e2e.mbox INDEX_PATH=/tmp/e2e.db HOST=127.0.0.1 PORT=8137 \
  .venv/bin/python -m mboxviewer.main
```
Expected: server starts immediately; `GET http://127.0.0.1:8137/api/status` returns
`ready: true` shortly. Then verify:
```bash
curl -s http://127.0.0.1:8137/api/status
curl -s -D - -o /dev/null "http://127.0.0.1:8137/api/messages/1/attachments/0?inline=1" | grep -i content-disposition
```
Expected: status JSON with `ready:true`; the attachment responds with
`content-disposition: inline; filename="doc.pdf"`.

- [ ] **Step 2: Visually verify in the browser**

Open http://127.0.0.1:8137 and confirm: the message opens, the `doc.pdf` attachment
shows a **View** button, and clicking it renders the PDF inline in the reader pane.
A download link is also present. Stop the dev server (Ctrl+C) when done; remove
`/tmp/e2e.mbox` and `/tmp/e2e.db*`.

- [ ] **Step 3: Rebuild and restart the real container (reuses the existing 101MB index)**

```bash
docker compose down 2>/dev/null; MBOX_FILE=/dev/null docker compose down 2>/dev/null || true
./run.sh   # rebuilds the image and restarts; the existing mbox-index volume is reused
```
Expected: because `index_is_current` matches the persisted index, the container does
**not** re-index — the logs show no `Building index`, the server comes up within
seconds, and `curl -s http://localhost:9000/api/status` returns `ready: true` with the
real message count (~54k). Confirm in the browser that the status bar is hidden (ready)
and that opening a message with a PDF attachment shows the inline **View** preview.

---

## Self-Review Notes

- **Spec coverage:** background thread + serve-immediately (Task 4 `create_app`) ✓;
  `/api/status` shape (Task 4) ✓; progress from `(count, bytes_done)` (Task 3) +
  `IndexStatus` (Task 1) ✓; thread-safe SQLite via thread-local connections (Task 2) ✓;
  status bar + live refresh (Task 5 `pollStatus`) ✓; inline disposition + `?inline=1`
  (Task 4) ✓; inline PDF iframe in reader pane (Task 5 `viewPdf`) ✓; no schema/meta
  change so existing index reused (Task 6 verification) ✓; tests for status (sync,
  background, reuse) and inline (Task 4) ✓.
- **Type/name consistency:** `IndexStatus` methods (`start/update/finish/fail/mark_ready/snapshot`)
  used identically in `api._run_index`; `progress(count, bytes_done)` ↔ `status.update(messages, bytes_done)`
  arity matches; `_content_disposition(filename, inline=False)` signature matches both call sites;
  `create_app(settings, index_in_background=True)` used consistently in app + tests; frontend ids
  (`status-bar`, `reader-pdf`) match between `index.html`, `app.js`, and `style.css`.
- **No placeholders:** every code step is complete; commands include expected output.
```
