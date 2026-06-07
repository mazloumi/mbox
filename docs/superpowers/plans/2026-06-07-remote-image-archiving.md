# Remote Image Archiving (durable) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in pass that downloads remote images (skipping tracking pixels) into a **durable host-folder archive** separate from the disposable index, so cached images display offline and survive dropping/rebuilding the index.

**Architecture:** A new `AssetStore` owns `<archive_dir>/archive.db` (asset metadata + a `source_size`/`source_mtime` marker) and bytes under `<archive_dir>/assets/`, independent of the index DB. `archive.run_archive` walks indexed messages, skips trackers before fetching, downloads the rest with a bounded thread pool, and short-circuits when the mbox is unchanged. The render path rewrites cached remote images to a local `/api/asset/{hash}` endpoint. A UI button triggers it.

**Tech Stack:** Python 3.9-compatible, stdlib `urllib`/`http.server`/`concurrent.futures`, SQLite, FastAPI. Tests: pytest + a local stub HTTP image server.

**Spec:** `docs/superpowers/specs/2026-06-07-remote-image-archiving-design.md`

---

## File Structure

```
src/mboxviewer/
  assets.py      # NEW — url hashing/normalize, extract_image_refs, is_tracking_pixel,
                 #       fetch_image, byte cache I/O (by archive_dir), rewrite_cached_images
  assetstore.py  # NEW — AssetStore: archive.db (assets + archive_meta), thread-local conns
  archive.py     # NEW — ArchiveStatus + run_archive (short-circuit, scan, skip, download)
  config.py      # MODIFY — Settings.archive_dir + ARCHIVE_DIR env
  store.py       # MODIFY — all_message_spans()
  api.py         # MODIFY — AssetStore wiring, archive/asset routes, cached-image rewrite
  static/        # MODIFY — "Archive remote images" button + confirm + progress
  Dockerfile, docker-compose.yml, run.sh, .env.example, README.md, CLAUDE.md  # MODIFY
tests/
  conftest.py        # MODIFY — image_server fixture
  test_assets.py     # NEW
  test_assetstore.py # NEW
  test_archive.py    # NEW
  test_store.py      # MODIFY — all_message_spans
  test_api.py        # MODIFY — archive/asset routes + cached-image render
```

`assets.py` = pure image/URL logic + byte cache. `assetstore.py` = the archive DB (sole
accessor). `store.py` stays the index DB's sole accessor. `archive.py` = orchestration.

---

## Task 1: assets.py — hashing, URL extraction, tracker detection

**Files:** Create `src/mboxviewer/assets.py`; Test `tests/test_assets.py`.

- [ ] **Step 1: Write failing tests — `tests/test_assets.py`:**
```python
from mboxviewer.assets import url_hash, normalize_url, extract_image_refs, is_tracking_pixel


def test_url_hash_stable_and_hex():
    h = url_hash("https://x.example/a.png")
    assert h == url_hash("https://x.example/a.png")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_normalize_protocol_relative():
    assert normalize_url("//host/a.png") == "https://host/a.png"
    assert normalize_url("https://h/a.png") == "https://h/a.png"


def test_extract_image_refs_img_and_css():
    html = ('<img src="https://a.example/1.png" width="120" height="80">'
            '<img src="//b.example/2.png">'
            '<img src="cid:embedded">'
            '<div style="background:url(https://c.example/3.png)"></div>')
    refs = extract_image_refs(html)
    urls = [u for (u, w, h) in refs]
    assert "https://a.example/1.png" in urls
    assert "https://b.example/2.png" in urls
    assert "https://c.example/3.png" in urls
    assert all("cid:" not in u for u in urls)
    a = next(r for r in refs if r[0] == "https://a.example/1.png")
    assert a[1] == 120 and a[2] == 80


def test_is_tracking_pixel():
    assert is_tracking_pixel("https://x/p.gif", 1, 1) is True
    assert is_tracking_pixel("https://x/p.gif", 2, 600) is True
    assert is_tracking_pixel("https://track.example/o.gif", None, None) is True
    assert is_tracking_pixel("https://x.example/logo.png", 300, 100) is False
    assert is_tracking_pixel("https://x.example/logo.png", None, None) is False
```

- [ ] **Step 2: Run tests, verify they FAIL** (`ModuleNotFoundError`).
Run: `.venv/bin/pytest tests/test_assets.py -v`

- [ ] **Step 3: Write `src/mboxviewer/assets.py`:**
```python
import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

TRACKER_HOSTS = (
    "track.", "tracking.", "click.", "open.", "px.", "pixel.", "beacon.",
    "list-manage.com", "sendgrid.net", "mailgun.org", "sparkpostmail.com",
)


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def normalize_url(url):
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _is_remote(url):
    return url.startswith(("http://", "https://", "//"))


def _dim(value):
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


class _ImgRefParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        d = dict(attrs)
        src = d.get("src")
        if src and _is_remote(src):
            self.refs.append((normalize_url(src), _dim(d.get("width")), _dim(d.get("height"))))


def extract_image_refs(html):
    """Return [(normalized_url, width|None, height|None)] for remote <img> and CSS url()."""
    parser = _ImgRefParser()
    parser.feed(html or "")
    refs = list(parser.refs)
    for m in re.finditer(r'url\(\s*["\']?\s*((?:https?:)?//[^)"\']+)', html or "", re.IGNORECASE):
        refs.append((normalize_url(m.group(1)), None, None))
    return refs


def is_tracking_pixel(url, width, height):
    if (width is not None and width <= 2) or (height is not None and height <= 2):
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(t in host for t in TRACKER_HOSTS)
```

- [ ] **Step 4: Run tests, verify they PASS (4 tests).**
Run: `.venv/bin/pytest tests/test_assets.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/assets.py tests/test_assets.py
git commit -m "feat: assets url hashing, image-ref extraction, tracker detection"
```

---

## Task 2: assets.py — fetch_image + byte cache (by archive_dir)

**Files:** Modify `src/mboxviewer/assets.py`; Modify `tests/conftest.py`; Add tests to `tests/test_assets.py`.

- [ ] **Step 1: Append the `image_server` fixture to `tests/conftest.py`** (add the imports if missing):
```python
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@pytest.fixture
def image_server():
    """Stub HTTP server. Records requested paths. /notimage* -> text/html,
    /big* -> 11MB image, else -> small image/png bytes."""
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if self.path.startswith("/notimage"):
                body, ctype = b"<html>nope</html>", "text/html"
            elif self.path.startswith("/big"):
                body, ctype = b"x" * (11 * 1024 * 1024), "image/png"
            else:
                body, ctype = b"FAKEIMAGEBYTES", "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    yield (f"http://127.0.0.1:{port}", requested)
    srv.shutdown()
```

- [ ] **Step 2: Write failing tests — append to `tests/test_assets.py`:**
```python
from mboxviewer.assets import fetch_image, write_asset_bytes, read_asset_bytes, assets_dir


def test_fetch_image_success(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/logo.png")
    assert res.ok and res.content_type == "image/png" and res.data == b"FAKEIMAGEBYTES"


def test_fetch_image_rejects_non_image(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/notimage.html")
    assert res.ok is False and "image" in res.error


def test_fetch_image_rejects_oversize(image_server):
    base, _ = image_server
    res = fetch_image(f"{base}/big.png", max_bytes=1024)
    assert res.ok is False and "large" in res.error


def test_fetch_image_network_error_is_caught():
    res = fetch_image("http://127.0.0.1:9/none.png", timeout=1)
    assert res.ok is False and res.error


def test_asset_byte_cache_roundtrip(tmp_path):
    archive_dir = str(tmp_path / "arch")
    write_asset_bytes(archive_dir, "abc123", b"hello")
    assert read_asset_bytes(archive_dir, "abc123") == b"hello"
    assert read_asset_bytes(archive_dir, "missing") is None
    assert assets_dir(archive_dir).endswith("assets")
```

- [ ] **Step 3: Run tests, verify they FAIL** (ImportError).
Run: `.venv/bin/pytest tests/test_assets.py -v`

- [ ] **Step 4: Append to `src/mboxviewer/assets.py`:**
```python
import io
import os
import urllib.request

MAX_ASSET_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT = 10


class FetchResult:
    def __init__(self, ok, content_type=None, data=None, error=None):
        self.ok = ok
        self.content_type = content_type
        self.data = data
        self.error = error


def fetch_image(url, timeout=FETCH_TIMEOUT, max_bytes=MAX_ASSET_BYTES):
    """Download an image. Never raises; returns a FetchResult. Honors HTTP(S)_PROXY env."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mbox-viewer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return FetchResult(False, error=f"not an image: {ctype or 'unknown'}")
            buf = io.BytesIO()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    return FetchResult(False, error="too large")
            return FetchResult(True, content_type=ctype, data=buf.getvalue())
    except Exception as exc:  # noqa: BLE001 - any network/parse error is a failed fetch
        return FetchResult(False, error=str(exc))


def assets_dir(archive_dir):
    return os.path.join(archive_dir, "assets")


def asset_path(archive_dir, h):
    return os.path.join(assets_dir(archive_dir), h)


def write_asset_bytes(archive_dir, h, data):
    os.makedirs(assets_dir(archive_dir), exist_ok=True)
    with open(asset_path(archive_dir, h), "wb") as f:
        f.write(data)


def read_asset_bytes(archive_dir, h):
    try:
        with open(asset_path(archive_dir, h), "rb") as f:
            return f.read()
    except OSError:
        return None
```

- [ ] **Step 5: Run tests, verify they PASS.**
Run: `.venv/bin/pytest tests/test_assets.py -v`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/assets.py tests/conftest.py tests/test_assets.py
git commit -m "feat: fetch_image with caps + archive-dir byte cache; image_server fixture"
```

---

## Task 3: assets.py — rewrite_cached_images

**Files:** Modify `src/mboxviewer/assets.py`; Add tests to `tests/test_assets.py`.

- [ ] **Step 1: Write failing tests — append to `tests/test_assets.py`:**
```python
from mboxviewer.assets import rewrite_cached_images


def test_rewrite_replaces_only_cached():
    cached_url = "https://a.example/cached.png"
    uncached_url = "https://b.example/uncached.png"
    h = url_hash(cached_url)
    html = (f'<img src="{cached_url}">'
            f'<img src="{uncached_url}">'
            f'<div style="background:url({cached_url})"></div>')
    out = rewrite_cached_images(html, {h})
    assert f'/api/asset/{h}' in out
    assert cached_url not in out
    assert uncached_url in out


def test_rewrite_handles_protocol_relative():
    url = "//c.example/p.png"
    h = url_hash(normalize_url(url))
    out = rewrite_cached_images(f'<img src="{url}">', {h})
    assert f'/api/asset/{h}' in out


def test_rewrite_noop_when_nothing_cached():
    html = '<img src="https://a.example/x.png">'
    assert rewrite_cached_images(html, set()) == html
```

- [ ] **Step 2: Run tests, verify they FAIL** (ImportError).
Run: `.venv/bin/pytest tests/test_assets.py -k rewrite -v`

- [ ] **Step 3: Append to `src/mboxviewer/assets.py`:**
```python
def rewrite_cached_images(html, cached_hashes):
    """Replace remote <img src> and CSS url() whose url_hash is in cached_hashes with
    the local /api/asset/<hash> endpoint. Leaves uncached refs untouched."""
    if not html or not cached_hashes:
        return html or ""

    def repl_src(m):
        prefix, quote, url = m.group(1), m.group(2), m.group(3)
        h = url_hash(normalize_url(url))
        if h in cached_hashes:
            return f"{prefix}{quote}/api/asset/{h}{quote}"
        return m.group(0)

    def repl_css(m):
        h = url_hash(normalize_url(m.group(1)))
        if h in cached_hashes:
            return f'url("/api/asset/{h}")'
        return m.group(0)

    html = re.sub(r'(\ssrc\s*=\s*)(["\'])((?:https?:)?//[^"\']*)\2', repl_src, html, flags=re.IGNORECASE)
    html = re.sub(r'url\(\s*["\']?\s*((?:https?:)?//[^)"\']+)["\']?\s*\)', repl_css, html, flags=re.IGNORECASE)
    return html
```

- [ ] **Step 4: Run tests, verify they PASS.**
Run: `.venv/bin/pytest tests/test_assets.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/assets.py tests/test_assets.py
git commit -m "feat: rewrite cached remote images to local /api/asset URLs"
```

---

## Task 4: config.archive_dir + Store.all_message_spans + AssetStore

**Files:** Modify `src/mboxviewer/config.py`, `src/mboxviewer/store.py`; Create `src/mboxviewer/assetstore.py`; Modify `tests/test_store.py`; Create `tests/test_assetstore.py`.

- [ ] **Step 1: Write failing tests.**

Add to `tests/test_config.py`:
```python
def test_archive_dir_default_and_env(monkeypatch):
    monkeypatch.delenv("ARCHIVE_DIR", raising=False)
    assert load_settings().archive_dir == "/archive"
    monkeypatch.setenv("ARCHIVE_DIR", "/tmp/arch")
    assert load_settings().archive_dir == "/tmp/arch"
```

Add to `tests/test_store.py`:
```python
def test_all_message_spans(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    spans = s.all_message_spans()
    assert len(spans) == 2 and spans[0]["length"] > 0
```

Create `tests/test_assetstore.py`:
```python
from mboxviewer.assetstore import AssetStore


def _astore(tmp_path):
    a = AssetStore(str(tmp_path / "arch"))
    a.create_schema()
    return a


def test_upsert_and_lookup(tmp_path):
    a = _astore(tmp_path)
    a.upsert_asset("h1", "https://x/a.png", "image/png", 10, 100, 50, "ok", None, "t")
    a.commit()
    assert a.asset_status("h1") == "ok"
    assert a.asset_status("nope") is None
    assert a.get_asset("h1")["content_type"] == "image/png"
    a.upsert_asset("h1", "https://x/a.png", "image/png", 10, 100, 50, "failed", "boom", "t2")
    a.commit()
    assert a.asset_status("h1") == "failed"


def test_cached_hashes_and_counts(tmp_path):
    a = _astore(tmp_path)
    a.upsert_asset("ok1", "u1", "image/png", 1, None, None, "ok", None, "t")
    a.upsert_asset("ok2", "u2", "image/png", 1, None, None, "ok", None, "t")
    a.upsert_asset("sk1", "u3", None, None, 1, 1, "skipped", None, "t")
    a.upsert_asset("fa1", "u4", None, None, None, None, "failed", "x", "t")
    a.commit()
    assert a.cached_asset_hashes({"ok1", "fa1", "missing"}) == {"ok1"}
    assert a.cached_asset_hashes(set()) == set()
    assert a.asset_counts() == {"ok": 2, "skipped": 1, "failed": 1, "total": 4}


def test_archive_meta(tmp_path):
    a = _astore(tmp_path)
    assert a.get_archive_meta() is None
    a.set_archive_meta(12345, 67890)
    assert a.get_archive_meta() == {"source_size": 12345, "source_mtime": 67890}
```

- [ ] **Step 2: Run tests, verify they FAIL.**
Run: `.venv/bin/pytest tests/test_config.py tests/test_store.py tests/test_assetstore.py -k "archive or span or upsert or cached or meta" -v`

- [ ] **Step 3a: Modify `src/mboxviewer/config.py`** — add `archive_dir`:
```python
@dataclass
class Settings:
    mbox_path: str
    index_path: str
    archive_dir: str = "/archive"
    host: str = "0.0.0.0"
    port: int = 9000


def load_settings() -> Settings:
    return Settings(
        mbox_path=os.environ.get("MBOX_PATH", "/data/mail.mbox"),
        index_path=os.environ.get("INDEX_PATH", "/index/index.db"),
        archive_dir=os.environ.get("ARCHIVE_DIR", "/archive"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "9000")),
    )
```

- [ ] **Step 3b: Add `all_message_spans` to `src/mboxviewer/store.py`** (e.g. after `get_attachments`):
```python
    def all_message_spans(self):
        return self.conn.execute("SELECT offset, length FROM messages").fetchall()
```

- [ ] **Step 3c: Create `src/mboxviewer/assetstore.py`:**
```python
import os
import sqlite3
import threading

ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  url_hash TEXT PRIMARY KEY,
  url TEXT,
  content_type TEXT,
  size INTEGER,
  width INTEGER,
  height INTEGER,
  status TEXT,
  error TEXT,
  fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT);
"""


class AssetStore:
    """Owns archive.db (asset metadata + archive_meta) in the durable archive dir."""

    def __init__(self, archive_dir):
        os.makedirs(archive_dir, exist_ok=True)
        self._db_path = os.path.join(archive_dir, "archive.db")
        self._local = threading.local()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
        return c

    def create_schema(self):
        self.conn.executescript(ASSET_SCHEMA)
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def upsert_asset(self, url_hash, url, content_type, size, width, height, status, error, fetched_at):
        self.conn.execute(
            "INSERT INTO assets(url_hash,url,content_type,size,width,height,status,error,fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url_hash) DO UPDATE SET"
            " url=excluded.url, content_type=excluded.content_type, size=excluded.size,"
            " width=excluded.width, height=excluded.height, status=excluded.status,"
            " error=excluded.error, fetched_at=excluded.fetched_at",
            (url_hash, url, content_type, size, width, height, status, error, fetched_at))

    def get_asset(self, url_hash):
        return self.conn.execute("SELECT * FROM assets WHERE url_hash=?", (url_hash,)).fetchone()

    def asset_status(self, url_hash):
        row = self.conn.execute("SELECT status FROM assets WHERE url_hash=?", (url_hash,)).fetchone()
        return row["status"] if row else None

    def cached_asset_hashes(self, url_hashes):
        hs = list(url_hashes)
        if not hs:
            return set()
        placeholders = ",".join("?" * len(hs))
        rows = self.conn.execute(
            f"SELECT url_hash FROM assets WHERE status='ok' AND url_hash IN ({placeholders})", hs).fetchall()
        return {r["url_hash"] for r in rows}

    def asset_counts(self):
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM assets GROUP BY status").fetchall()
        by = {r["status"]: r["c"] for r in rows}
        return {"ok": by.get("ok", 0), "skipped": by.get("skipped", 0),
                "failed": by.get("failed", 0), "total": sum(by.values())}

    def get_archive_meta(self):
        rows = self.conn.execute("SELECT key, value FROM archive_meta").fetchall()
        d = {r["key"]: r["value"] for r in rows}
        if "source_size" in d and "source_mtime" in d:
            return {"source_size": int(d["source_size"]), "source_mtime": int(d["source_mtime"])}
        return None

    def set_archive_meta(self, size, mtime):
        for key, value in (("source_size", str(size)), ("source_mtime", str(mtime))):
            self.conn.execute(
                "INSERT INTO archive_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()
```

- [ ] **Step 4: Run tests, verify they PASS** (config, store, assetstore).
Run: `.venv/bin/pytest tests/test_config.py tests/test_store.py tests/test_assetstore.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/config.py src/mboxviewer/store.py src/mboxviewer/assetstore.py \
        tests/test_config.py tests/test_store.py tests/test_assetstore.py
git commit -m "feat: archive_dir setting, all_message_spans, and AssetStore (archive.db)"
```

---

## Task 5: archive.py — ArchiveStatus + run_archive (with short-circuit)

**Files:** Create `src/mboxviewer/archive.py`; Test `tests/test_archive.py`.

- [ ] **Step 1: Write failing tests — `tests/test_archive.py`:**
```python
import io
from email.message import EmailMessage
from email.generator import BytesGenerator

from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.assetstore import AssetStore
from mboxviewer.archive import ArchiveStatus, run_archive
from mboxviewer.assets import url_hash, read_asset_bytes


def _mbox_with_html(tmp_path, html):
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body"); m.add_alternative(html, subtype="html")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "a.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    return str(p)


def _setup(tmp_path, mbox):
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    store = Store(settings.index_path); store.create_schema(); build_index(settings, store)
    asset_store = AssetStore(settings.archive_dir); asset_store.create_schema()
    return settings, store, asset_store


def test_archive_downloads_real_and_skips_tracker(tmp_path, image_server):
    base, requested = image_server
    html = f'<img src="{base}/logo.png"><img src="{base}/pixel.gif" width="1" height="1">'
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, html))
    status = ArchiveStatus()
    run_archive(settings, store, astore, status)
    s = status.snapshot()
    assert s["running"] is False and s["error"] is None
    assert s["downloaded"] == 1 and s["skipped"] == 1
    logo_h = url_hash(f"{base}/logo.png")
    assert astore.asset_status(logo_h) == "ok"
    assert read_asset_bytes(settings.archive_dir, logo_h) == b"FAKEIMAGEBYTES"
    assert astore.asset_status(url_hash(f"{base}/pixel.gif")) == "skipped"
    assert "/logo.png" in requested and "/pixel.gif" not in requested


def test_archive_records_failed(tmp_path, image_server):
    base, _ = image_server
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, f'<img src="{base}/notimage.html">'))
    run_archive(settings, store, astore, ArchiveStatus())
    assert astore.asset_status(url_hash(f"{base}/notimage.html")) == "failed"


def test_archive_resumable_and_short_circuits(tmp_path, image_server):
    base, requested = image_server
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, f'<img src="{base}/logo.png">'))
    run_archive(settings, store, astore, ArchiveStatus())
    requested.clear()
    # second run: mbox unchanged, no failures -> short-circuit, zero requests, scanned stays 0
    status2 = ArchiveStatus()
    run_archive(settings, store, astore, status2)
    assert requested == []
    s = status2.snapshot()
    assert s["running"] is False and s["messages_scanned"] == 0 and s["downloaded"] == 1
```

- [ ] **Step 2: Run tests, verify they FAIL** (ModuleNotFoundError).
Run: `.venv/bin/pytest tests/test_archive.py -v`

- [ ] **Step 3: Write `src/mboxviewer/archive.py`:**
```python
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .reader import read_message, get_display_body
from .assets import extract_image_refs, is_tracking_pixel, fetch_image, url_hash, write_asset_bytes

MAX_WORKERS = 12


def _now():
    return datetime.now(timezone.utc).isoformat()


class ArchiveStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._messages_scanned = 0
        self._total_messages = 0
        self._urls_seen = 0
        self._downloaded = 0
        self._skipped = 0
        self._failed = 0
        self._error = None

    def start(self, total):
        with self._lock:
            self._running = True
            self._total_messages = total
            self._messages_scanned = self._urls_seen = 0
            self._downloaded = self._skipped = self._failed = 0
            self._error = None

    def mark_running(self):
        with self._lock:
            self._running = True

    def complete_from_counts(self, counts):
        """Short-circuit finish: reflect existing archive counts, no work done."""
        with self._lock:
            self._running = False
            self._downloaded = counts.get("ok", 0)
            self._skipped = counts.get("skipped", 0)
            self._failed = counts.get("failed", 0)
            self._error = None

    def _inc(self, name):
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def inc_scanned(self): self._inc("_messages_scanned")
    def inc_urls_seen(self): self._inc("_urls_seen")
    def inc_downloaded(self): self._inc("_downloaded")
    def inc_skipped(self): self._inc("_skipped")
    def inc_failed(self): self._inc("_failed")

    def finish(self):
        with self._lock:
            self._running = False

    def fail(self, error):
        with self._lock:
            self._running = False
            self._error = str(error)

    def running(self):
        with self._lock:
            return self._running

    def snapshot(self):
        with self._lock:
            return {
                "running": self._running,
                "messages_scanned": self._messages_scanned,
                "total_messages": self._total_messages,
                "urls_seen": self._urls_seen,
                "downloaded": self._downloaded,
                "skipped": self._skipped,
                "failed": self._failed,
                "error": self._error,
            }


def run_archive(settings, store, asset_store, status):
    """Archive remote images. Short-circuits when the mbox is unchanged and nothing
    failed. All asset_store writes happen on this thread; workers only fetch."""
    try:
        cur_size = os.path.getsize(settings.mbox_path)
        cur_mtime = int(os.path.getmtime(settings.mbox_path))
        meta = asset_store.get_archive_meta()
        counts = asset_store.asset_counts()
        if (meta and meta["source_size"] == cur_size and meta["source_mtime"] == cur_mtime
                and counts["failed"] == 0):
            status.complete_from_counts(counts)
            return

        spans = store.all_message_spans()
        status.start(len(spans))
        seen = set()
        to_download = []  # (hash, url, width, height)
        for row in spans:
            try:
                msg = read_message(settings.mbox_path, row["offset"], row["length"])
                mime, content = get_display_body(msg)
                if mime == "text/html":
                    for url, width, height in extract_image_refs(content):
                        h = url_hash(url)
                        if h in seen:
                            continue
                        seen.add(h)
                        status.inc_urls_seen()
                        if asset_store.asset_status(h) in ("ok", "skipped"):
                            continue
                        if is_tracking_pixel(url, width, height):
                            asset_store.upsert_asset(h, url, None, None, width, height, "skipped", None, _now())
                            status.inc_skipped()
                        else:
                            to_download.append((h, url, width, height))
            except Exception as exc:  # noqa: BLE001 - skip a bad message, keep going
                sys.stderr.write(f"archive scan skip: {exc}\n")
            status.inc_scanned()
        asset_store.commit()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_image, url): (h, url, w, ht)
                       for (h, url, w, ht) in to_download}
            for future in as_completed(futures):
                h, url, width, height = futures[future]
                res = future.result()
                if res.ok:
                    write_asset_bytes(settings.archive_dir, h, res.data)
                    asset_store.upsert_asset(h, url, res.content_type, len(res.data),
                                             width, height, "ok", None, _now())
                    status.inc_downloaded()
                else:
                    asset_store.upsert_asset(h, url, None, None, width, height, "failed", res.error, _now())
                    status.inc_failed()
        asset_store.commit()
        asset_store.set_archive_meta(cur_size, cur_mtime)
        status.finish()
    except Exception as exc:  # noqa: BLE001 - surface any fatal error to the UI
        sys.stderr.write(f"archive failed: {exc}\n")
        status.fail(exc)
```

- [ ] **Step 4: Run tests, verify they PASS (3 tests).**
Run: `.venv/bin/pytest tests/test_archive.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/archive.py tests/test_archive.py
git commit -m "feat: archive worker with tracker-skip, download, and unchanged-mbox short-circuit"
```

---

## Task 6: api.py — archive/asset routes + cached-image rewrite

**Files:** Modify `src/mboxviewer/api.py`; Add tests to `tests/test_api.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_api.py`:**
```python
import io as _io
from email.message import EmailMessage
from email.generator import BytesGenerator


def _client_for_html(tmp_path, html):
    m = EmailMessage()
    m["Subject"] = "img"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body"); m.add_alternative(html, subtype="html")
    buf = _io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "img.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    return TestClient(create_app(settings, index_in_background=False)), settings


def test_archive_status_idle(client):
    s = client.get("/api/archive/status").json()
    assert s["running"] is False and s["downloaded"] == 0


def test_archive_start_returns_started(client):
    assert client.post("/api/archive/start").json()["started"] in (True, False)


def test_asset_endpoint_serves_cached_bytes(tmp_path):
    from mboxviewer import assets
    c, settings = _client_for_html(tmp_path, '<p>hi</p>')
    h = assets.url_hash("https://x.example/logo.png")
    astore = c.app.state.asset_store
    assets.write_asset_bytes(settings.archive_dir, h, b"IMGDATA")
    astore.upsert_asset(h, "https://x.example/logo.png", "image/png", 7, None, None, "ok", None, "t")
    astore.commit()
    r = c.get(f"/api/asset/{h}")
    assert r.status_code == 200 and r.content == b"IMGDATA"
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/api/asset/" + "0" * 64).status_code == 404
    assert c.get("/api/asset/not-hex").status_code == 404


def test_message_detail_rewrites_cached_image(tmp_path):
    from mboxviewer import assets
    url = "https://x.example/logo.png"
    c, settings = _client_for_html(tmp_path, f'<img src="{url}">')
    h = assets.url_hash(url)
    astore = c.app.state.asset_store
    assets.write_asset_bytes(settings.archive_dir, h, b"IMGDATA")
    astore.upsert_asset(h, url, "image/png", 7, None, None, "ok", None, "t")
    astore.commit()
    mid = c.get("/api/messages", params={"label": "Inbox"}).json()["messages"][0]["id"]
    body = c.get(f"/api/messages/{mid}").json()["body_html"]
    assert f"/api/asset/{h}" in body and url not in body
```

- [ ] **Step 2: Run tests, verify they FAIL.**
Run: `.venv/bin/pytest tests/test_api.py -k "archive or asset or rewrites" -v`

- [ ] **Step 3: Modify `src/mboxviewer/api.py`.**
Add imports near the existing ones:
```python
from . import assets
from .assetstore import AssetStore
from .archive import ArchiveStatus, run_archive
```
Inside `create_app`, after `app.state.status = status`, add:
```python
    asset_store = AssetStore(settings.archive_dir)
    asset_store.create_schema()
    archive_status = ArchiveStatus()
    archive_lock = threading.Lock()
    app.state.asset_store = asset_store
    app.state.archive_status = archive_status
```
In `message_detail`, replace:
```python
        mime, content = get_display_body(msg)
        body_html = _render_body(mime, content, allow_remote=allow_remote)
```
with:
```python
        mime, content = get_display_body(msg)
        if mime == "text/html":
            refs = assets.extract_image_refs(content)
            if refs:
                cached = asset_store.cached_asset_hashes({assets.url_hash(u) for (u, _, _) in refs})
                if cached:
                    content = assets.rewrite_cached_images(content, cached)
        body_html = _render_body(mime, content, allow_remote=allow_remote)
```
Add these routes before `@app.get("/")`:
```python
    @app.post("/api/archive/start")
    def archive_start():
        with archive_lock:
            if archive_status.running():
                return {"started": False}
            archive_status.mark_running()
            threading.Thread(target=run_archive,
                             args=(settings, store, asset_store, archive_status),
                             daemon=True).start()
            return {"started": True}

    @app.get("/api/archive/status")
    def archive_status_route():
        return archive_status.snapshot()

    @app.get("/api/asset/{asset_hash}")
    def get_asset(asset_hash: str):
        if not re.fullmatch(r"[0-9a-f]{64}", asset_hash):
            raise HTTPException(404, "not found")
        row = asset_store.get_asset(asset_hash)
        if row is None or row["status"] != "ok":
            raise HTTPException(404, "not cached")
        data = assets.read_asset_bytes(settings.archive_dir, asset_hash)
        if data is None:
            raise HTTPException(404, "asset missing")
        return Response(
            content=data, media_type=row["content_type"] or "application/octet-stream",
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
```

- [ ] **Step 4: Run tests, verify they PASS** (new + full api suite).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Full suite.**
Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: archive/asset routes and cached-image rewrite via AssetStore"
```

---

## Task 7: Packaging — archive host mount + config + docs

**Files:** Modify `Dockerfile`, `docker-compose.yml`, `run.sh`, `.env.example`, `README.md`, `CLAUDE.md`.

- [ ] **Step 1: `Dockerfile`** — add `ARCHIVE_DIR` to the ENV block:
Change:
```dockerfile
ENV PYTHONPATH=/app/src \
    MBOX_PATH=/data/mail.mbox \
    INDEX_PATH=/index/index.db \
    PORT=9000
```
to:
```dockerfile
ENV PYTHONPATH=/app/src \
    MBOX_PATH=/data/mail.mbox \
    INDEX_PATH=/index/index.db \
    ARCHIVE_DIR=/archive \
    PORT=9000
```

- [ ] **Step 2: `docker-compose.yml`** — add the archive bind mount + container env.
Change the `volumes:` and `environment:` of the `mbox-viewer` service to:
```yaml
    volumes:
      - "${MBOX_FILE:-/path/to/your-mail.mbox}:/data/mail.mbox:ro"
      - mbox-index:/index
      - "${ARCHIVE_HOST_DIR:-./archive}:/archive"
    environment:
      - MBOX_PATH=/data/mail.mbox
      - INDEX_PATH=/index/index.db
      - ARCHIVE_DIR=/archive
```
(`ARCHIVE_HOST_DIR` is the host path mounted read-write to `/archive`; `run.sh` sets it.)

- [ ] **Step 3: `run.sh`** — default the host archive dir next to the mbox and export it.
After the line `export PORT="${PORT:-9000}"`, add:
```bash
# Durable image archive: a host folder next to the mbox by default (override with ARCHIVE_HOST_DIR).
export ARCHIVE_HOST_DIR="${ARCHIVE_HOST_DIR:-$(dirname "$RESOLVED")/mbox-viewer-archive}"
mkdir -p "$ARCHIVE_HOST_DIR"
```
And add an echo line in the startup banner (next to the existing `echo "viewer ..."`):
```bash
echo "archive   : $ARCHIVE_HOST_DIR"
```

- [ ] **Step 4: `.env.example`** — document the archive dir. Append:
```
# Durable host folder for the offline image archive (created if missing).
# Defaults (via run.sh) to a folder next to your mbox; set to override:
ARCHIVE_HOST_DIR=/path/to/mbox-viewer-archive
```

- [ ] **Step 5: `README.md`** — add a short "Durability & backups" section after "Re-indexing":
```markdown
## Durability & offline archive

The search index is disposable — it rebuilds from the mbox. The **remote image
archive** is not (it can only be re-created over the network), so it lives in a
separate **host folder** (`ARCHIVE_HOST_DIR`, by default next to your mbox) holding
`archive.db` + `assets/`. Click **"Archive remote images"** in the viewer to download
them (tracking pixels are skipped; set `HTTPS_PROXY` to route through a VPN).

Your complete offline copy is **the mbox file + the archive folder**. Back up those two
and you can delete the originals in Gmail, drop/rebuild the index, or move machines —
everything still renders offline. Re-running "Archive remote images" on an unchanged
mbox is an instant no-op (it records the mbox size/mtime and skips re-downloading).
```

- [ ] **Step 6: `CLAUDE.md`** — under "Implemented enhancements" add a bullet, and add a
contract about the archive being durable/separate. Append to the "Implemented
enhancements" list:
```markdown
- **Durable remote-image archiving** — opt-in `POST /api/archive/start` downloads remote
  images (skipping trackers) into a separate host-folder archive (`AssetStore` /
  `archive.db` + `assets/`, env `ARCHIVE_DIR`); `message_detail` rewrites cached remote
  `<img>` to `/api/asset/{hash}`. Durable across index rebuilds; short-circuits on an
  unchanged mbox. Backup unit = mbox + archive folder; the index is disposable.
```

- [ ] **Step 7: Verify nothing broke.**
Run: `.venv/bin/pytest -q` (still green — these are config/docs/packaging files)
Run: `bash -n run.sh && echo "run.sh ok"`

- [ ] **Step 8: Commit.**
```bash
git add Dockerfile docker-compose.yml run.sh .env.example README.md CLAUDE.md
git commit -m "feat: durable archive host mount, ARCHIVE_DIR config, and backup docs"
```

---

## Task 8: Frontend — Archive button, confirm, progress

**Files:** Modify `src/mboxviewer/static/index.html`, `static/app.js`, `static/style.css`.

- [ ] **Step 1: `index.html`** — replace the toolbar:
```html
  <div id="toolbar">
    <button id="toggle-folders" type="button" title="Show/hide folders">☰ Folders</button>
  </div>
```
with:
```html
  <div id="toolbar">
    <button id="toggle-folders" type="button" title="Show/hide folders">☰ Folders</button>
    <button id="archive-images" type="button" title="Download remote images for offline viewing">Archive remote images</button>
    <span id="archive-status"></span>
  </div>
```

- [ ] **Step 2: `app.js`** — add DOM refs near the other `getElementById` lines:
```javascript
const archiveBtn = document.getElementById("archive-images");
const archiveStatusEl = document.getElementById("archive-status");
```
Add this block just above the final `pollStatus();` line:
```javascript
// --- Opt-in remote-image archiving ---
async function pollArchive() {
  try {
    const s = await getJSON("/api/archive/status");
    const n = (x) => Number(x).toLocaleString();
    if (s.running) {
      archiveBtn.disabled = true;
      archiveStatusEl.textContent =
        `Archiving images… ${n(s.messages_scanned)}/${n(s.total_messages)} · ` +
        `${n(s.downloaded)} saved · ${n(s.skipped)} skipped · ${n(s.failed)} failed`;
      setTimeout(pollArchive, 2000);
    } else {
      archiveBtn.disabled = false;
      if (s.error) {
        archiveStatusEl.textContent = "Archive failed: " + s.error;
      } else if (s.downloaded || s.skipped || s.failed) {
        archiveStatusEl.textContent =
          `Archived: ${n(s.downloaded)} saved, ${n(s.skipped)} skipped, ${n(s.failed)} failed`;
      }
    }
  } catch (e) { /* ignore transient errors */ }
}

archiveBtn.addEventListener("click", async () => {
  const ok = confirm(
    "Archive remote images for offline viewing?\n\n" +
    "This downloads images from senders' servers so they display even when you're " +
    "offline or the images are later removed. It may signal to senders that these " +
    "emails were opened (your IP and the time). Tracking pixels are skipped.\n\nProceed?");
  if (!ok) return;
  try { await fetch("/api/archive/start", { method: "POST" }); } catch (e) { /* ignore */ }
  pollArchive();
});

pollArchive();  // reflect any in-progress/finished archive on load
```

- [ ] **Step 3: `style.css`** — append:
```css
#archive-images { font-size: 12px; line-height: 1.4; padding: 2px 8px; margin-left: 6px;
  cursor: pointer; background: #f3f3f3; border: 1px solid #ccc; border-radius: 4px; }
#archive-images:disabled { opacity: 0.6; cursor: default; }
#archive-status { margin-left: 8px; font-size: 12px; color: #555; }
```

- [ ] **Step 4: Verify suite green + braces balanced.**
Run: `.venv/bin/pytest -q`
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: 'Archive remote images' button with confirm and progress"
```

---

## Task 9: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Local end-to-end with a stub image server.**
In terminal 1, start a stub server + mbox referencing it (leave running):
```bash
.venv/bin/python - <<'PY'
import io, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import EmailMessage
from email.generator import BytesGenerator
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body, ctype = b"REALIMAGE", "image/png"
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 8765), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
html = '<img src="http://127.0.0.1:8765/logo.png"><img src="http://127.0.0.1:8765/p.gif" width="1" height="1">'
m = EmailMessage(); m["Subject"]="Pic"; m["From"]="a@x.com"; m["To"]="b@x.com"
m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
m.set_content("b"); m.add_alternative(html, subtype="html")
buf=io.BytesIO(); BytesGenerator(buf).flatten(m); data=buf.getvalue()
open("/tmp/img.mbox","wb").write(b"From - x\n"+data+(b"" if data.endswith(b"\n") else b"\n")+b"\n")
print("stub on :8765, /tmp/img.mbox written"); time.sleep(3600)
PY
```
In terminal 2:
```bash
rm -rf /tmp/img.db* /tmp/imgarch
PYTHONPATH=src MBOX_PATH=/tmp/img.mbox INDEX_PATH=/tmp/img.db ARCHIVE_DIR=/tmp/imgarch \
  HOST=127.0.0.1 PORT=8137 .venv/bin/python -m mboxviewer.main &
sleep 3
curl -s -X POST http://127.0.0.1:8137/api/archive/start; echo
sleep 2
curl -s http://127.0.0.1:8137/api/archive/status; echo     # downloaded=1, skipped=1, running=false
MID=$(curl -s "http://127.0.0.1:8137/api/messages" | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['messages'][0]['id'])")
curl -s "http://127.0.0.1:8137/api/messages/$MID" | grep -o '/api/asset/[0-9a-f]\{64\}' | head -1
ls /tmp/imgarch/assets/                                     # the cached image file exists
```
Expected: status `downloaded:1, skipped:1`; body contains a `/api/asset/<hash>` URL; the
asset bytes are on disk under `/tmp/imgarch/assets/`. Confirm durability: stop the dev
server, `rm -rf /tmp/img.db*` (drop the index), restart against the SAME archive dir, POST
`/api/archive/start` again → `/api/archive/status` shows it short-circuited (no new
downloads) and the message still renders the cached image. Then stop everything; `rm -rf
/tmp/img.mbox /tmp/img.db* /tmp/imgarch`.

- [ ] **Step 2: Browser check.** Open http://127.0.0.1:8137 (before cleanup), click
"Archive remote images", confirm the dialog, watch progress finish, open the "Pic"
message and confirm the image renders (from `/api/asset/...`) with no broken icon and the
1×1 pixel absent.

- [ ] **Step 3: Redeploy the real container with the archive mount.**
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Expected: `run.sh` prints an `archive : <path-next-to-mbox>` line, serves in ~seconds with
no re-index (`/api/status` ready, ~54k), and the new `/archive` mount is present. Optionally
trigger a real archive run from the UI (behind a VPN if desired) and confirm a previously
blocked image renders offline. The archive persists at the host folder for backup.

---

## Self-Review Notes

- **Spec coverage:** durable separate archive store (Task 4 `AssetStore` + Task 7 host
  mount) ✓; image bytes by URL-hash dedup (Tasks 2/5) ✓; extract/tracker/fetch/rewrite
  (Tasks 1–3) ✓; skip-trackers-before-fetch asserted (Task 5) ✓; short-circuit on
  unchanged mbox via `archive_meta` (Tasks 4/5, asserted zero requests) ✓; API
  start/status/asset + render rewrite (Task 6) ✓; frontend button/confirm/progress
  (Task 8) ✓; proxy via urllib env (Task 2) ✓; backup/delete-Gmail docs (Task 7) ✓;
  index untouched / rebuildable (assets in separate DB — Task 4) ✓.
- **Type/name consistency:** `assets` functions (`url_hash`/`normalize_url`/`extract_image_refs`/`is_tracking_pixel`/`fetch_image`/`FetchResult`/`assets_dir`/`asset_path`/`write_asset_bytes`/`read_asset_bytes`/`rewrite_cached_images`) used identically in archive/api; byte funcs take **archive_dir** consistently; `AssetStore` methods match across archive/api; `Settings.archive_dir` + `ARCHIVE_DIR` (container) vs `ARCHIVE_HOST_DIR` (compose host path) used consistently; `/api/asset/{asset_hash}` param avoids shadowing `assets.url_hash`; `ArchiveStatus` snapshot keys match the frontend readout.
- **No placeholders:** every code/test step is complete; commands include expected output.
```
