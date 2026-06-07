# mbox Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Dockerized web app that indexes a single large Google Takeout `.mbox` file (mounted read-only) and lets a user browse Gmail-label "folders", read emails, view/download attachments, and full-text search bodies + attachment text in the browser.

**Architecture:** One-time streaming indexer records each message's byte offset/length and writes metadata + an SQLite FTS5 full-text index. A FastAPI server browses/searches via SQLite and reads individual messages by seeking to their byte offset (low memory on 10GB+ files). A static vanilla-JS 3-pane frontend renders sanitized HTML email in a sandboxed iframe.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, stdlib `mailbox`/`email`, SQLite FTS5, `bleach` (sanitize), `pypdf` + `python-docx` (attachment text). Dev/test: `pytest`, `httpx`, `reportlab` (generate PDF fixtures).

---

## File Structure

```
mbox/
  requirements.txt              # runtime deps
  requirements-dev.txt          # test deps
  Dockerfile
  docker-compose.yml
  .env.example
  README.md
  pytest.ini
  src/mboxviewer/
    __init__.py
    config.py                   # Settings + load_settings()
    store.py                    # Store: SQLite schema, writes, queries, search
    reader.py                   # message-span scan, read_message, iter_attachments, display body
    extract.py                  # extract_text() + html_to_text()
    sanitize.py                 # sanitize_html()
    indexer.py                  # build_index(), index_is_current()
    api.py                      # create_app() + routes
    main.py                     # uvicorn entrypoint
    static/
      index.html
      app.js
      style.css
  tests/
    conftest.py                 # sample_mbox fixture (real MIME + pdf/docx attachments)
    test_store.py
    test_reader.py
    test_extract.py
    test_sanitize.py
    test_indexer.py
    test_api.py
```

Responsibilities (one purpose each): `config` = settings; `store` = all SQLite access; `reader` = bytes→`EmailMessage`; `extract` = bytes→text; `sanitize` = unsafe HTML→safe HTML; `indexer` = orchestrate scan→store; `api` = HTTP; `main` = process entrypoint; `static/*` = UI.

---

## Task 1: Project scaffold + config

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `src/mboxviewer/__init__.py`, `src/mboxviewer/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Create dependency + pytest files**

`requirements.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
bleach==6.2.0
pypdf==5.1.0
python-docx==1.1.2
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.3.4
httpx==0.28.1
reportlab==4.2.5
```

`pytest.ini`:
```ini
[pytest]
pythonpath = src
testpaths = tests
```

`src/mboxviewer/__init__.py`:
```python
```
(empty file)

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:
```python
from mboxviewer.config import load_settings


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("MBOX_PATH", "/data/x.mbox")
    monkeypatch.setenv("INDEX_PATH", "/index/i.db")
    monkeypatch.setenv("PORT", "9000")
    s = load_settings()
    assert s.mbox_path == "/data/x.mbox"
    assert s.index_path == "/index/i.db"
    assert s.port == 9000
    assert s.host == "0.0.0.0"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pip install -r requirements-dev.txt && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.config'`

- [ ] **Step 4: Write minimal implementation**

`src/mboxviewer/config.py`:
```python
import os
from dataclasses import dataclass


@dataclass
class Settings:
    mbox_path: str
    index_path: str
    host: str = "0.0.0.0"
    port: int = 8000


def load_settings() -> Settings:
    return Settings(
        mbox_path=os.environ.get("MBOX_PATH", "/data/mail.mbox"),
        index_path=os.environ.get("INDEX_PATH", "/index/index.db"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini src/mboxviewer/__init__.py src/mboxviewer/config.py tests/test_config.py
git commit -m "feat: project scaffold and config loader"
```

---

## Task 2: Shared test fixture (sample mbox)

**Files:**
- Create: `tests/conftest.py`

This fixture is reused by reader, indexer, and API tests. It builds a real mbox with two messages, Gmail labels, an HTML body, a PDF attachment containing `INVOICE 12345`, and a DOCX attachment containing `QUARTERLY REPORT`.

- [ ] **Step 1: Write the fixture**

`tests/conftest.py`:
```python
import io
from email.message import EmailMessage
from email.generator import BytesGenerator

import pytest
from reportlab.pdfgen import canvas
import docx


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _make_docx(text: str) -> bytes:
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph(text)
    d.save(buf)
    return buf.getvalue()


def _email(subject, sender, to, labels, html, attachments):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["X-Gmail-Labels"] = labels
    msg.set_content("plain text body")
    msg.add_alternative(html, subtype="html")
    for filename, maintype, subtype, data in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def _serialize(msg) -> bytes:
    buf = io.BytesIO()
    BytesGenerator(buf).flatten(msg)
    return buf.getvalue()


@pytest.fixture
def sample_mbox(tmp_path):
    pdf = _make_pdf("INVOICE 12345")
    dx = _make_docx("QUARTERLY REPORT")
    m1 = _email(
        "Welcome aboard", "alice@example.com", "bob@example.com",
        "Inbox,Important",
        "<html><body><p>Hello <b>Bob</b></p></body></html>",
        [("invoice.pdf", "application", "pdf", pdf)],
    )
    m2 = _email(
        "Q1 numbers", "carol@example.com", "bob@example.com",
        "Inbox,Work",
        "<html><body><p>See attached report</p></body></html>",
        [("report.docx", "application",
          "vnd.openxmlformats-officedocument.wordprocessingml.document", dx)],
    )
    path = tmp_path / "sample.mbox"
    with open(path, "wb") as f:
        for m in (m1, m2):
            f.write(b"From - Mon Jan 01 10:00:00 2024\n")
            f.write(_serialize(m))
            if not _serialize(m).endswith(b"\n"):
                f.write(b"\n")
            f.write(b"\n")
    return str(path)
```

- [ ] **Step 2: Sanity-check the fixture imports cleanly**

Run: `pytest tests/ -q --collect-only`
Expected: collection succeeds with no import errors (0 tests is fine at this point).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add sample mbox fixture with pdf/docx attachments"
```

---

## Task 3: Store (SQLite schema, writes, queries, search)

**Files:**
- Create: `src/mboxviewer/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
from mboxviewer.store import Store


def _store(tmp_path):
    s = Store(str(tmp_path / "i.db"))
    s.create_schema()
    return s


def test_message_and_label_roundtrip(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(0, 100, "<id1>", "Hi", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    lid = s.add_label("Inbox")
    s.link_label(mid, lid)
    s.add_fts(mid, "Hi", "a@x.com", "b@x.com", "hello world", "")
    s.commit()
    assert s.list_labels() == [("Inbox", 1)]
    rows = s.list_messages("Inbox", limit=10, offset=0)
    assert len(rows) == 1 and rows[0]["subject"] == "Hi"


def test_add_label_is_idempotent(tmp_path):
    s = _store(tmp_path)
    assert s.add_label("Work") == s.add_label("Work")


def test_search_matches_body_and_attachment_text(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(0, 100, "<id>", "Invoice", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    s.link_label(mid, s.add_label("Inbox"))
    s.add_fts(mid, "Invoice", "a@x.com", "b@x.com", "see body", "INVOICE 12345")
    s.commit()
    assert [r["id"] for r in s.search("12345", None, 10, 0)] == [mid]
    assert [r["id"] for r in s.search("body", None, 10, 0)] == [mid]
    assert s.search("nomatch", None, 10, 0) == []


def test_get_message_and_attachments(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(5, 50, "<id>", "S", "a", "b", "2024-01-01T10:00:00", "raw")
    s.add_attachment(mid, 0, "invoice.pdf", "application/pdf", 999)
    s.commit()
    row = s.get_message_row(mid)
    assert row["offset"] == 5 and row["length"] == 50
    atts = s.get_attachments(mid)
    assert atts[0]["filename"] == "invoice.pdf" and atts[0]["idx"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.store'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/store.py`:
```python
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  offset INTEGER NOT NULL,
  length INTEGER NOT NULL,
  message_id TEXT,
  subject TEXT,
  from_addr TEXT,
  to_addr TEXT,
  date TEXT,
  date_raw TEXT
);
CREATE TABLE IF NOT EXISTS labels (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS message_labels (
  message_id INTEGER NOT NULL REFERENCES messages(id),
  label_id INTEGER NOT NULL REFERENCES labels(id),
  PRIMARY KEY (message_id, label_id)
);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  idx INTEGER NOT NULL,
  filename TEXT,
  mime TEXT,
  size INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  subject, from_addr, to_addr, body, attachments, content=''
);
"""


def _fts_query(q: str) -> str:
    terms = [t for t in q.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in terms)


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")

    def create_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def add_message(self, offset, length, message_id, subject, from_addr, to_addr, date, date_raw):
        cur = self.conn.execute(
            "INSERT INTO messages(offset,length,message_id,subject,from_addr,to_addr,date,date_raw)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (offset, length, message_id, subject, from_addr, to_addr, date, date_raw))
        return cur.lastrowid

    def add_label(self, name):
        self.conn.execute("INSERT OR IGNORE INTO labels(name) VALUES(?)", (name,))
        return self.conn.execute("SELECT id FROM labels WHERE name=?", (name,)).fetchone()["id"]

    def link_label(self, message_id, label_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO message_labels(message_id,label_id) VALUES(?,?)",
            (message_id, label_id))

    def add_attachment(self, message_id, idx, filename, mime, size):
        self.conn.execute(
            "INSERT INTO attachments(message_id,idx,filename,mime,size) VALUES(?,?,?,?,?)",
            (message_id, idx, filename, mime, size))

    def add_fts(self, rowid, subject, from_addr, to_addr, body, attachments):
        self.conn.execute(
            "INSERT INTO messages_fts(rowid,subject,from_addr,to_addr,body,attachments)"
            " VALUES(?,?,?,?,?,?)", (rowid, subject, from_addr, to_addr, body, attachments))

    def list_labels(self):
        rows = self.conn.execute(
            "SELECT l.name AS name, COUNT(*) AS c FROM labels l "
            "JOIN message_labels ml ON ml.label_id=l.id GROUP BY l.id ORDER BY l.name").fetchall()
        return [(r["name"], r["c"]) for r in rows]

    def list_messages(self, label, limit, offset):
        if label:
            return self.conn.execute(
                "SELECT m.* FROM messages m JOIN message_labels ml ON ml.message_id=m.id "
                "JOIN labels l ON l.id=ml.label_id WHERE l.name=? "
                "ORDER BY m.date DESC LIMIT ? OFFSET ?", (label, limit, offset)).fetchall()
        return self.conn.execute(
            "SELECT * FROM messages ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()

    def search(self, query, label, limit, offset):
        match = _fts_query(query)
        if not match:
            return []
        if label:
            sql = ("SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                   "JOIN message_labels ml ON ml.message_id=m.id "
                   "JOIN labels l ON l.id=ml.label_id "
                   "WHERE l.name=? AND messages_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?")
            params = (label, match, limit, offset)
        else:
            sql = ("SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                   "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?")
            params = (match, limit, offset)
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    def get_message_row(self, message_id):
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

    def get_attachments(self, message_id):
        return self.conn.execute(
            "SELECT * FROM attachments WHERE message_id=? ORDER BY idx", (message_id,)).fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/store.py tests/test_store.py
git commit -m "feat: SQLite store with FTS5 search"
```

---

## Task 4: Mbox reader (span scan, read by offset, attachments, display body)

**Files:**
- Create: `src/mboxviewer/reader.py`
- Test: `tests/test_reader.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_reader.py`:
```python
from mboxviewer.reader import (
    iter_message_spans, read_message, iter_attachments, get_display_body, parse_labels,
)


def test_spans_find_two_messages(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    assert len(spans) == 2
    for offset, length in spans:
        assert length > 0


def test_read_message_parses_headers(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    assert msg["subject"] == "Welcome aboard"
    assert msg["from"] == "alice@example.com"
    assert parse_labels(msg["x-gmail-labels"]) == ["Inbox", "Important"]


def test_iter_attachments_returns_payload(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    atts = list(iter_attachments(msg))
    assert len(atts) == 1
    idx, filename, mime, payload = atts[0]
    assert idx == 0 and filename == "invoice.pdf"
    assert mime == "application/pdf" and payload[:4] == b"%PDF"


def test_display_body_prefers_html(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    mime, content = get_display_body(msg)
    assert mime == "text/html" and "<b>Bob</b>" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.reader'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/reader.py`:
```python
import email
from email import policy


def iter_message_spans(path):
    """Yield (offset, length) byte spans for each message in an mbox file.

    A message boundary is a line starting with b'From ' that is at the start of
    the file or immediately preceded by a blank line (mboxrd convention).
    """
    with open(path, "rb") as f:
        msg_start = None
        prev_blank = True
        pos = 0
        while True:
            line = f.readline()
            if not line:
                break
            if line.startswith(b"From ") and prev_blank:
                if msg_start is not None:
                    yield (msg_start, pos - msg_start)
                msg_start = pos
            prev_blank = line in (b"\n", b"\r\n")
            pos += len(line)
        if msg_start is not None:
            yield (msg_start, pos - msg_start)


def read_message(path, offset, length):
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(length)
    nl = raw.find(b"\n")
    if raw.startswith(b"From ") and nl != -1:
        raw = raw[nl + 1:]
    return email.message_from_bytes(raw, policy=policy.default)


def parse_labels(header_value):
    if not header_value:
        return []
    return [p.strip() for p in str(header_value).split(",") if p.strip()]


def iter_attachments(msg):
    """Yield (idx, filename, mime, payload_bytes) for attachment parts in walk order."""
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or (filename and disposition != "inline"):
            payload = part.get_payload(decode=True) or b""
            yield idx, filename or f"attachment-{idx}", part.get_content_type(), payload
            idx += 1


def get_display_body(msg):
    """Return (mime, content) preferring HTML, falling back to plain text."""
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ("text/plain", "")
    return (body.get_content_type(), body.get_content())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/reader.py tests/test_reader.py
git commit -m "feat: mbox reader with offset-based message access"
```

---

## Task 5: Attachment text extraction + HTML-to-text

**Files:**
- Create: `src/mboxviewer/extract.py`
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_extract.py`:
```python
import io
import docx
from reportlab.pdfgen import canvas
from mboxviewer.extract import extract_text, html_to_text


def _pdf(text):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _docx(text):
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph(text)
    d.save(buf)
    return buf.getvalue()


def test_extract_plain_text():
    assert "hello" in extract_text("a.txt", "text/plain", b"hello world")


def test_extract_pdf():
    out = extract_text("a.pdf", "application/pdf", _pdf("INVOICE 12345"))
    assert "12345" in out


def test_extract_docx():
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    out = extract_text("a.docx", mime, _docx("QUARTERLY REPORT"))
    assert "QUARTERLY" in out


def test_extract_unsupported_returns_empty():
    assert extract_text("a.bin", "application/octet-stream", b"\x00\x01") == ""


def test_extract_handles_corrupt_pdf_gracefully():
    assert extract_text("a.pdf", "application/pdf", b"not a real pdf") == ""


def test_html_to_text_strips_tags():
    assert html_to_text("<p>Hello <b>world</b></p>").strip() == "Hello world"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.extract'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/extract.py`:
```python
import io
from html.parser import HTMLParser

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html or "")
    return " ".join("".join(p.parts).split())


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    return "\n".join(par.text for par in d.paragraphs)


def extract_text(filename: str, mime: str, data: bytes) -> str:
    """Best-effort plain-text extraction. Returns '' for unsupported or on any error."""
    mime = (mime or "").lower()
    try:
        if mime == "application/pdf":
            return _pdf_text(data)
        if mime == _DOCX_MIME:
            return _docx_text(data)
        if mime.startswith("text/html"):
            return html_to_text(data.decode("utf-8", "replace"))
        if mime.startswith("text/"):
            return data.decode("utf-8", "replace")
    except Exception:
        return ""
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/extract.py tests/test_extract.py
git commit -m "feat: attachment text extraction (pdf/docx/text) and html-to-text"
```

---

## Task 6: HTML sanitization

**Files:**
- Create: `src/mboxviewer/sanitize.py`
- Test: `tests/test_sanitize.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_sanitize.py`:
```python
from mboxviewer.sanitize import sanitize_html


def test_strips_script():
    out = sanitize_html("<p>hi</p><script>alert(1)</script>", allow_remote=False)
    assert "<script>" not in out and "alert" not in out
    assert "hi" in out


def test_blocks_remote_image_by_default():
    out = sanitize_html('<img src="http://tracker.example/x.gif">', allow_remote=False)
    assert "tracker.example" not in out


def test_allows_remote_image_when_opted_in():
    out = sanitize_html('<img src="http://imgs.example/x.png">', allow_remote=True)
    assert "imgs.example" in out


def test_keeps_basic_formatting():
    out = sanitize_html("<p>Hello <b>Bob</b></p>", allow_remote=False)
    assert "<b>" in out and "Bob" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sanitize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.sanitize'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/sanitize.py`:
```python
import re
import bleach

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p", "br", "div", "span", "img", "table", "thead", "tbody", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "u", "font",
]
ALLOWED_ATTRS = {
    "*": ["style", "align", "width", "height", "color"],
    "a": ["href", "title", "target"],
    "img": ["src", "alt", "width", "height"],
    "font": ["color", "face", "size"],
}

_REMOTE_SRC = re.compile(r'\ssrc\s*=\s*(["\'])\s*https?://[^"\']*\1', re.IGNORECASE)


def sanitize_html(html: str, allow_remote: bool = False) -> str:
    cleaned = bleach.clean(
        html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    if not allow_remote:
        cleaned = _REMOTE_SRC.sub(' src=""', cleaned)
    return cleaned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sanitize.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/sanitize.py tests/test_sanitize.py
git commit -m "feat: HTML sanitization blocking remote content by default"
```

---

## Task 7: Indexer (scan → store) + staleness check

**Files:**
- Create: `src/mboxviewer/indexer.py`
- Test: `tests/test_indexer.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_indexer.py`:
```python
import os
from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.indexer import build_index, index_is_current


def _build(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    count = build_index(settings, store)
    return settings, store, count


def test_build_index_counts_messages(tmp_path, sample_mbox):
    _, store, count = _build(tmp_path, sample_mbox)
    assert count == 2


def test_index_creates_labels_with_counts(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    labels = dict(store.list_labels())
    assert labels["Inbox"] == 2
    assert labels["Important"] == 1
    assert labels["Work"] == 1


def test_index_enables_attachment_text_search(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    assert len(store.search("12345", None, 10, 0)) == 1       # pdf text
    assert len(store.search("QUARTERLY", None, 10, 0)) == 1   # docx text


def test_index_records_attachments(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    rows = store.list_messages("Important", 10, 0)
    atts = store.get_attachments(rows[0]["id"])
    assert atts[0]["filename"] == "invoice.pdf"


def test_index_is_current_detects_staleness(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    os.utime(sample_mbox, (0, 0))  # change mtime
    assert index_is_current(settings, store) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indexer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.indexer'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/indexer.py`:
```python
import os
from email.utils import parsedate_to_datetime

from .reader import iter_message_spans, read_message, iter_attachments, get_display_body, parse_labels
from .extract import extract_text, html_to_text


def _iso_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return None


def _body_text(msg):
    mime, content = get_display_body(msg)
    return html_to_text(content) if mime == "text/html" else content


def build_index(settings, store, progress=None):
    count = 0
    for offset, length in iter_message_spans(settings.mbox_path):
        try:
            msg = read_message(settings.mbox_path, offset, length)
            date_raw = msg["date"]
            mid = store.add_message(
                offset, length, msg["message-id"], msg["subject"],
                msg["from"], msg["to"], _iso_date(date_raw), date_raw)
            for name in parse_labels(msg["x-gmail-labels"]):
                store.link_label(mid, store.add_label(name))
            att_texts = []
            for idx, filename, mime, payload in iter_attachments(msg):
                store.add_attachment(mid, idx, filename, mime, len(payload))
                att_texts.append(extract_text(filename, mime, payload))
            store.add_fts(
                mid, msg["subject"] or "", msg["from"] or "", msg["to"] or "",
                _body_text(msg), "\n".join(att_texts))
            count += 1
            if progress and count % 500 == 0:
                progress(count)
        except Exception as exc:  # malformed message: log and skip
            print(f"skipping message at offset {offset}: {exc}")
    store.set_meta("source_size", str(os.path.getsize(settings.mbox_path)))
    store.set_meta("source_mtime", str(int(os.path.getmtime(settings.mbox_path))))
    store.commit()
    return count


def index_is_current(settings, store):
    try:
        size = store.get_meta("source_size")
        mtime = store.get_meta("source_mtime")
    except Exception:
        return False
    if size is None or mtime is None:
        return False
    return (size == str(os.path.getsize(settings.mbox_path))
            and mtime == str(int(os.path.getmtime(settings.mbox_path))))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indexer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/indexer.py tests/test_indexer.py
git commit -m "feat: streaming indexer with staleness detection"
```

---

## Task 8: API (FastAPI app + routes)

**Files:**
- Create: `src/mboxviewer/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient
from mboxviewer.config import Settings
from mboxviewer.api import create_app


@pytest.fixture
def client(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    return TestClient(create_app(settings))


def test_labels_endpoint(client):
    data = client.get("/api/labels").json()
    by_name = {d["name"]: d["count"] for d in data}
    assert by_name["Inbox"] == 2 and by_name["Work"] == 1


def test_messages_listing_for_label(client):
    data = client.get("/api/messages", params={"label": "Important"}).json()
    assert len(data["messages"]) == 1
    assert data["messages"][0]["subject"] == "Welcome aboard"


def test_message_detail_sanitizes_body(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    detail = client.get(f"/api/messages/{mid}").json()
    assert "<b>Bob</b>" in detail["body_html"]
    assert detail["attachments"][0]["filename"] == "invoice.pdf"


def test_attachment_download(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    resp = client.get(f"/api/messages/{mid}/attachments/0")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"
    assert "invoice.pdf" in resp.headers["content-disposition"]


def test_search_finds_attachment_text(client):
    data = client.get("/api/search", params={"q": "12345"}).json()
    assert len(data["messages"]) == 1


def test_index_html_served(client):
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mboxviewer.api'`

- [ ] **Step 3: Write minimal implementation**

`src/mboxviewer/api.py`:
```python
import os
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _msg_summary(row):
    return {
        "id": row["id"], "subject": row["subject"], "from": row["from_addr"],
        "to": row["to_addr"], "date": row["date"],
    }


def create_app(settings):
    app = FastAPI(title="mbox viewer")
    store = Store(settings.index_path)
    store.create_schema()
    if not index_is_current(settings, store):
        print("Building index...")
        n = build_index(settings, store, progress=lambda c: print(f"  indexed {c}"))
        print(f"Index complete: {n} messages")
    app.state.store = store
    app.state.settings = settings

    @app.get("/api/labels")
    def labels():
        return [{"name": n, "count": c} for n, c in store.list_labels()]

    @app.get("/api/messages")
    def messages(label: str | None = None, page: int = 1, page_size: int = 50):
        offset = (page - 1) * page_size
        rows = store.list_messages(label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/search")
    def search(q: str = Query(...), label: str | None = None, page: int = 1, page_size: int = 50):
        offset = (page - 1) * page_size
        rows = store.search(q, label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/messages/{message_id}")
    def message_detail(message_id: int, allow_remote: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        msg = read_message(settings.mbox_path, row["offset"], row["length"])
        mime, content = get_display_body(msg)
        body_html = sanitize_html(content if mime == "text/html" else
                                  "<pre>" + content + "</pre>", allow_remote=allow_remote)
        atts = [{"idx": a["idx"], "filename": a["filename"], "mime": a["mime"], "size": a["size"]}
                for a in store.get_attachments(message_id)]
        return {**_msg_summary(row), "body_html": body_html, "attachments": atts}

    @app.get("/api/messages/{message_id}/attachments/{idx}")
    def attachment(message_id: int, idx: int):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        msg = read_message(settings.mbox_path, row["offset"], row["length"])
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return Response(
                    content=payload, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        raise HTTPException(404, "attachment not found")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
```

- [ ] **Step 4: Create the static directory with a placeholder so StaticFiles mounts**

```bash
mkdir -p src/mboxviewer/static
printf '<!doctype html><title>mbox viewer</title><h1>mbox viewer</h1>' > src/mboxviewer/static/index.html
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/mboxviewer/api.py src/mboxviewer/static/index.html tests/test_api.py
git commit -m "feat: FastAPI routes for labels, messages, search, attachments"
```

---

## Task 9: Frontend (3-pane UI)

**Files:**
- Modify: `src/mboxviewer/static/index.html` (replace placeholder)
- Create: `src/mboxviewer/static/app.js`, `src/mboxviewer/static/style.css`

No unit tests (static assets); verified manually in Task 11.

- [ ] **Step 1: Write `index.html`**

`src/mboxviewer/static/index.html`:
```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>mbox viewer</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
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
    </section>
  </div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `style.css`**

`src/mboxviewer/static/style.css`:
```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; }
#app { display: grid; grid-template-columns: 220px 360px 1fr; height: 100vh; }
#labels, #list { border-right: 1px solid #ddd; overflow-y: auto; }
#labels { padding: 0 12px; }
#labels h2 { font-size: 13px; text-transform: uppercase; color: #666; }
#label-list, #message-list { list-style: none; margin: 0; padding: 0; }
#label-list li, #message-list li { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #f0f0f0; }
#label-list li:hover, #message-list li:hover { background: #f5f7ff; }
#label-list li.active, #message-list li.active { background: #e7efff; }
.count { color: #999; float: right; font-size: 12px; }
.subject { font-weight: 600; }
.meta { color: #777; font-size: 12px; }
#searchbar { padding: 10px; border-bottom: 1px solid #ddd; }
#q { width: 100%; padding: 6px 8px; }
#reader { display: flex; flex-direction: column; }
#reader-header { padding: 12px; border-bottom: 1px solid #eee; }
#reader-attachments { padding: 8px 12px; }
#reader-attachments a { display: inline-block; margin-right: 8px; padding: 4px 8px;
  background: #eef; border-radius: 4px; text-decoration: none; font-size: 13px; }
#reader-body { flex: 1; border: 0; width: 100%; }
```

- [ ] **Step 3: Write `app.js`**

`src/mboxviewer/static/app.js`:
```javascript
const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const q = document.getElementById("q");

let activeLabel = null;

async function getJSON(url) { return (await fetch(url)).json(); }

async function loadLabels() {
  const labels = await getJSON("/api/labels");
  labelList.innerHTML = "";
  for (const l of labels) {
    const li = document.createElement("li");
    li.innerHTML = `${l.name}<span class="count">${l.count}</span>`;
    li.onclick = () => { activeLabel = l.name; setActive(labelList, li); loadMessages(); };
    labelList.appendChild(li);
  }
}

function setActive(container, el) {
  container.querySelectorAll("li").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
}

function renderMessages(messages) {
  messageList.innerHTML = "";
  for (const m of messages) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">${escapeHtml(m.from || "")} — ${escapeHtml((m.date || "").slice(0, 10))}</div>`;
    li.onclick = () => { setActive(messageList, li); openMessage(m.id); };
    messageList.appendChild(li);
  }
}

async function loadMessages() {
  const url = activeLabel ? `/api/messages?label=${encodeURIComponent(activeLabel)}` : "/api/messages";
  renderMessages((await getJSON(url)).messages);
}

async function runSearch() {
  const term = q.value.trim();
  if (!term) return loadMessages();
  const url = `/api/search?q=${encodeURIComponent(term)}` +
    (activeLabel ? `&label=${encodeURIComponent(activeLabel)}` : "");
  renderMessages((await getJSON(url)).messages);
}

async function openMessage(id) {
  const m = await getJSON(`/api/messages/${id}`);
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
    <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>`;
  readerAtt.innerHTML = m.attachments.map(a =>
    `<a href="/api/messages/${id}/attachments/${a.idx}">${escapeHtml(a.filename)} (${a.size}b)</a>`).join("");
  readerBody.srcdoc = m.body_html;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

let searchTimer;
q.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });

loadLabels();
loadMessages();
```

- [ ] **Step 4: Confirm tests still pass (index.html still served)**

Run: `pytest tests/test_api.py::test_index_html_served -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: 3-pane frontend with search and sandboxed body rendering"
```

---

## Task 10: Entrypoint + Docker

**Files:**
- Create: `src/mboxviewer/main.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`

- [ ] **Step 1: Write the entrypoint**

`src/mboxviewer/main.py`:
```python
import uvicorn
from .config import load_settings
from .api import create_app


def main():
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the Dockerfile**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    MBOX_PATH=/data/mail.mbox \
    INDEX_PATH=/index/index.db \
    PORT=8000

EXPOSE 8000
CMD ["python", "-m", "mboxviewer.main"]
```

- [ ] **Step 3: Write docker-compose and env example**

`docker-compose.yml`:
```yaml
services:
  mbox-viewer:
    build: .
    ports:
      - "${PORT:-8000}:8000"
    volumes:
      - "${MBOX_FILE:?set MBOX_FILE to your .mbox path in .env}:/data/mail.mbox:ro"
      - mbox-index:/index
    environment:
      - MBOX_PATH=/data/mail.mbox
      - INDEX_PATH=/index/index.db

volumes:
  mbox-index:
```

`.env.example`:
```
# Absolute path on your host to the Google Takeout mbox file:
MBOX_FILE=/Users/you/Downloads/All mail Including Spam and Trash.mbox
# Port to expose the viewer on:
PORT=8000
```

- [ ] **Step 4: Write the README**

`README.md`:
```markdown
# mbox Viewer

Browse, search, and read a Google Takeout `.mbox` file in your browser. Runs in
Docker and reads the mbox file (mounted read-only) from your host machine. Gmail
labels become folders; full-text search covers message bodies and attachment text
(PDF/DOCX).

## Quick start

1. Copy `.env.example` to `.env` and set `MBOX_FILE` to the absolute path of your
   `.mbox` file on the host.
2. Build and run:

   ```bash
   docker compose up --build
   ```

3. On first run the app indexes the mbox (this can take several minutes for large
   files; watch the logs). The index is stored in a Docker volume and reused on
   later starts.
4. Open http://localhost:8000

## Development

```bash
pip install -r requirements-dev.txt
pytest
PYTHONPATH=src MBOX_PATH=/path/to.mbox INDEX_PATH=./index.db python -m mboxviewer.main
```
```

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS (all tests across the suite)

- [ ] **Step 6: Commit**

```bash
git add src/mboxviewer/main.py Dockerfile docker-compose.yml .env.example README.md
git commit -m "feat: docker packaging and entrypoint"
```

---

## Task 11: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Build the image**

Run: `docker compose build`
Expected: image builds without error.

- [ ] **Step 2: Run with a real (or fixture) mbox**

Create a small test mbox or point `MBOX_FILE` at a real Takeout export in `.env`, then:
Run: `docker compose up`
Expected logs: `Building index...`, `indexed N`, `Index complete`, then Uvicorn `Application startup complete`.

- [ ] **Step 3: Verify in the browser**

Open http://localhost:8000 and confirm:
- Folder list shows Gmail labels with counts.
- Clicking a label lists its messages; clicking a message renders the sanitized body in the iframe.
- Attachments appear as download links and download correctly.
- Searching a word known to be in a body or a PDF/DOCX attachment returns the message.

- [ ] **Step 4: Verify index reuse**

Run: `docker compose down && docker compose up`
Expected: second start does NOT rebuild the index (no `Building index...`), starts serving quickly.

---

## Self-Review Notes

- **Spec coverage:** Docker + read-only mount (Tasks 10/11) ✓; Gmail-label folders (reader `parse_labels`, indexer, `/api/labels` — Tasks 4/7/8) ✓; browse emails (Task 8) ✓; view sanitized body (Tasks 6/8/9) ✓; attachments view+download (Tasks 4/8/9) ✓; full-text incl. attachment text (Tasks 5/7, `search` — Tasks 3/7/8) ✓; byte-offset low-memory read for 10GB+ (reader Task 4) ✓; index staleness/re-index (Task 7) ✓; testing per component ✓.
- **Type consistency:** `iter_attachments` yields `(idx, filename, mime, payload)` and is consumed identically in indexer (Task 7) and API (Task 8); `Store` method names match across store/indexer/api; attachment index field is `idx` everywhere.
- **No placeholders:** every code step contains complete code; commands have expected output.
```
