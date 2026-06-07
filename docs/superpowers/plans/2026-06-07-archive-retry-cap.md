# Archive Retry Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After 3 failed download attempts an image becomes permanently `gave_up` (terminal): no longer retried, not counted as `failed` (so the unchanged-mbox short-circuit fires and the footer shows ✓), with an `unreachable` count in the footer.

**Architecture:** Add an `attempts` counter + a `gave_up` status to the assets table (migrated in place via `ALTER TABLE`); the archive worker increments attempts on failure and flips to `gave_up` at the cap, and skips `gave_up` like `ok`/`skipped`; the footer shows the new `unreachable` bucket.

**Tech Stack:** Python 3.9, SQLite, FastAPI, vanilla JS. Tests: pytest + the stub HTTP image server fixture.

**Spec:** `docs/superpowers/specs/2026-06-07-archive-retry-cap-design.md`

---

## File Structure

```
src/mboxviewer/assetstore.py   # MODIFY — attempts column + migration, upsert attempts, get_attempts, asset_counts gave_up
src/mboxviewer/archive.py      # MODIFY — MAX_FETCH_ATTEMPTS, skip gave_up, failed→gave_up at cap
src/mboxviewer/static/app.js   # MODIFY — footer 'unreachable' segment
tests/conftest.py              # MODIFY — image_server /fail* -> HTTP 500
tests/test_assetstore.py       # MODIFY — migration, attempts, gave_up count
tests/test_archive.py          # MODIFY — retry-then-give-up
```

No API route changes (`/api/archive/status.archived` already returns `asset_counts()`).

---

## Task 1: assetstore — attempts column, migration, gave_up count

**Files:** Modify `src/mboxviewer/assetstore.py`; Modify `tests/test_assetstore.py`.

- [ ] **Step 1: Write/adjust failing tests in `tests/test_assetstore.py`.**

(a) UPDATE the existing `test_cached_hashes_and_counts` — its final assertion must include the new `gave_up` key. Change:
```python
    assert a.asset_counts() == {"ok": 2, "skipped": 1, "failed": 1, "total": 4}
```
to:
```python
    assert a.asset_counts() == {"ok": 2, "skipped": 1, "failed": 1, "gave_up": 0, "total": 4}
```

(b) ADD these tests (add `import os` and `import sqlite3` at the top of the file if not present):
```python
def test_attempts_and_gave_up_count(tmp_path):
    a = AssetStore(str(tmp_path / "arch"))
    a.create_schema()
    a.upsert_asset("h", "u", None, None, None, None, "failed", "x", "t", attempts=2)
    a.commit()
    assert a.get_attempts("h") == 2
    assert a.get_attempts("missing") == 0
    a.upsert_asset("g", "u2", None, None, None, None, "gave_up", "y", "t", attempts=3)
    a.commit()
    assert a.asset_counts() == {"ok": 0, "skipped": 0, "failed": 1, "gave_up": 1, "total": 2}


def test_migration_adds_attempts_to_old_db(tmp_path):
    import os
    import sqlite3
    d = str(tmp_path / "arch")
    os.makedirs(d, exist_ok=True)
    # Simulate a pre-existing archive.db WITHOUT the attempts column.
    conn = sqlite3.connect(os.path.join(d, "archive.db"))
    conn.execute(
        "CREATE TABLE assets (url_hash TEXT PRIMARY KEY, url TEXT, content_type TEXT,"
        " size INTEGER, width INTEGER, height INTEGER, status TEXT NOT NULL, error TEXT,"
        " fetched_at TEXT)")
    conn.execute("INSERT INTO assets(url_hash, status) VALUES('h', 'ok')")
    conn.commit(); conn.close()
    a = AssetStore(d)
    a.create_schema()                      # must ALTER-add attempts with no data loss
    assert a.get_attempts("h") == 0        # existing row migrated to default 0
    assert a.asset_status("h") == "ok"
```

- [ ] **Step 2: Run tests, verify they FAIL.**
Run: `.venv/bin/pytest tests/test_assetstore.py -v`
Expected: FAIL (`get_attempts` missing / `upsert_asset` got unexpected kwarg `attempts` / asset_counts missing `gave_up`).

- [ ] **Step 3: Modify `src/mboxviewer/assetstore.py`.**

Add `attempts` to the `ASSET_SCHEMA` `CREATE TABLE` (after `fetched_at TEXT`):
```python
ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  url_hash TEXT PRIMARY KEY,
  url TEXT,
  content_type TEXT,
  size INTEGER,
  width INTEGER,
  height INTEGER,
  status TEXT NOT NULL,
  error TEXT,
  fetched_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT);
"""
```

Replace `create_schema` with the migrating version:
```python
    def create_schema(self):
        self.conn.executescript(ASSET_SCHEMA)
        try:
            self.conn.execute("ALTER TABLE assets ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB created it, or a prior migration ran)
        self.conn.commit()
```

Replace `upsert_asset` to add the `attempts` column/param (default 0):
```python
    def upsert_asset(self, url_hash, url, content_type, size, width, height, status, error,
                     fetched_at, attempts=0):
        self.conn.execute(
            "INSERT INTO assets(url_hash,url,content_type,size,width,height,status,error,fetched_at,attempts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url_hash) DO UPDATE SET"
            " url=excluded.url, content_type=excluded.content_type, size=excluded.size,"
            " width=excluded.width, height=excluded.height, status=excluded.status,"
            " error=excluded.error, fetched_at=excluded.fetched_at, attempts=excluded.attempts",
            (url_hash, url, content_type, size, width, height, status, error, fetched_at, attempts))
```

Add `get_attempts` (e.g. after `asset_status`):
```python
    def get_attempts(self, url_hash):
        row = self.conn.execute("SELECT attempts FROM assets WHERE url_hash=?", (url_hash,)).fetchone()
        return row["attempts"] if row else 0
```

Update `asset_counts` to include the `gave_up` bucket:
```python
    def asset_counts(self):
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM assets GROUP BY status").fetchall()
        by = {r["status"]: r["c"] for r in rows}
        return {"ok": by.get("ok", 0), "skipped": by.get("skipped", 0),
                "failed": by.get("failed", 0), "gave_up": by.get("gave_up", 0),
                "total": sum(by.values())}
```

- [ ] **Step 4: Run tests, verify they PASS** (full assetstore suite).
Run: `.venv/bin/pytest tests/test_assetstore.py -v`

- [ ] **Step 5: Run the full suite** (the api test that reads `asset_counts()` must still pass — it only reads `.ok`/`.total`).
Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/assetstore.py tests/test_assetstore.py
git commit -m "feat: assets attempts column (migrated) + gave_up status/count + get_attempts"
```

---

## Task 2: archive worker — retry cap → gave_up

**Files:** Modify `src/mboxviewer/archive.py`; Modify `tests/conftest.py`; Modify `tests/test_archive.py`.

- [ ] **Step 1: Add a `/fail*` (HTTP 500) branch to the `image_server` fixture in `tests/conftest.py`.**
In the `do_GET` handler, change:
```python
            if self.path.startswith("/notimage"):
                body, ctype = b"<html>nope</html>", "text/html"
```
to:
```python
            if self.path.startswith("/fail"):
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"error")
                return
            if self.path.startswith("/notimage"):
                body, ctype = b"<html>nope</html>", "text/html"
```
(A 500 makes `fetch_image` return `ok=False` with `skip=False` → a retriable `failed`, distinct from the non-image `skip` path.)

- [ ] **Step 2: Write the failing test — add to `tests/test_archive.py`:**
```python
def test_archive_retries_then_gives_up(tmp_path, image_server):
    base, requested = image_server
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, f'<img src="{base}/fail.png">'))
    h = url_hash(f"{base}/fail.png")
    run_archive(settings, store, astore, ArchiveStatus())   # attempt 1
    assert astore.asset_status(h) == "failed"
    run_archive(settings, store, astore, ArchiveStatus())   # attempt 2
    assert astore.asset_status(h) == "failed"
    run_archive(settings, store, astore, ArchiveStatus())   # attempt 3 -> give up
    assert astore.asset_status(h) == "gave_up"
    hits = requested.count("/fail.png")
    run_archive(settings, store, astore, ArchiveStatus())   # terminal -> short-circuit
    assert requested.count("/fail.png") == hits             # not requested again
    counts = astore.asset_counts()
    assert counts["failed"] == 0 and counts["gave_up"] == 1
```

- [ ] **Step 3: Run the test, verify it FAILS** (status stays "failed" on the 3rd run; the URL is re-requested on the 4th).
Run: `.venv/bin/pytest tests/test_archive.py::test_archive_retries_then_gives_up -v`

- [ ] **Step 4: Modify `src/mboxviewer/archive.py`.**

Add the constant next to `MAX_WORKERS`:
```python
MAX_WORKERS = 12
MAX_FETCH_ATTEMPTS = 3
```

In the scan loop, change the terminal-skip check from:
```python
                        if asset_store.asset_status(h) in ("ok", "skipped"):
                            continue
```
to:
```python
                        if asset_store.asset_status(h) in ("ok", "skipped", "gave_up"):
                            continue
```

In the download phase, change the failure branch from:
```python
                else:
                    asset_store.upsert_asset(h, url, None, None, width, height, "failed", res.error, _now())
                    status.inc_failed()
```
to:
```python
                else:
                    attempts = asset_store.get_attempts(h) + 1
                    failed_status = "gave_up" if attempts >= MAX_FETCH_ATTEMPTS else "failed"
                    asset_store.upsert_asset(h, url, None, None, width, height, failed_status,
                                             res.error, _now(), attempts=attempts)
                    status.inc_failed()
```
(Leave the `ok` and `res.skip` branches unchanged — they upsert with the default `attempts=0`.)

- [ ] **Step 5: Run tests, verify they PASS** (the new test + all existing archive tests).
Run: `.venv/bin/pytest tests/test_archive.py -v`

- [ ] **Step 6: Run the full suite.**
Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 7: Commit.**
```bash
git add src/mboxviewer/archive.py tests/conftest.py tests/test_archive.py
git commit -m "feat: cap fetch retries — mark images gave_up (terminal) after 3 attempts"
```

---

## Task 3: Frontend — 'unreachable' footer segment

**Files:** Modify `src/mboxviewer/static/app.js`.

No unit tests (static asset); verified in Task 4.

- [ ] **Step 1: Update the idle archive branch in `pollArchive` in `app.js`.**
Find:
```javascript
      const a = s.archived || { ok: 0, skipped: 0, failed: 0, total: 0 };
      if (!a.total) {
        archiveStateEl.textContent = "Images: not archived yet";
      } else {
        const breakdown = `Images: ${n(a.total)} total · ${n(a.ok)} archived · ` +
          `${n(a.skipped)} skipped · ${n(a.failed)} failed`;
        archiveStateEl.textContent = breakdown + (s.up_to_date ? " ✓" : " · click to update");
      }
```
Replace with:
```javascript
      const a = s.archived || { ok: 0, skipped: 0, failed: 0, gave_up: 0, total: 0 };
      if (!a.total) {
        archiveStateEl.textContent = "Images: not archived yet";
      } else {
        const breakdown = `Images: ${n(a.total)} total · ${n(a.ok)} archived · ` +
          `${n(a.skipped)} skipped · ${n(a.failed)} failed · ${n(a.gave_up)} unreachable`;
        archiveStateEl.textContent = breakdown + (s.up_to_date ? " ✓" : " · click to update");
      }
```

- [ ] **Step 2: Confirm the backend suite is still green + braces balanced.**
Run: `.venv/bin/pytest -q`
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`

- [ ] **Step 3: Commit.**
```bash
git add src/mboxviewer/static/app.js
git commit -m "feat: show 'unreachable' (gave-up) count in the footer archive breakdown"
```

---

## Task 4: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Local end-to-end with a 500-ing stub.**
Terminal 1 (stub that 500s for the image + an mbox referencing it, leave running):
```bash
.venv/bin/python - <<'PY'
import io, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import EmailMessage
from email.generator import BytesGenerator
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500); self.send_header("Content-Type","text/plain")
        self.send_header("Content-Length","3"); self.end_headers(); self.wfile.write(b"err")
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 8766), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
html = '<p>Hi</p><img src="http://127.0.0.1:8766/dead.png">'
m = EmailMessage(); m["Subject"]="Pic"; m["From"]="a@x.com"; m["To"]="b@x.com"
m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
m.set_content("b"); m.add_alternative(html, subtype="html")
buf=io.BytesIO(); BytesGenerator(buf).flatten(m); data=buf.getvalue()
open("/tmp/rc.mbox","wb").write(b"From - x\n"+data+(b"" if data.endswith(b"\n") else b"\n")+b"\n")
print("ready"); time.sleep(3600)
PY
```
Terminal 2:
```bash
rm -rf /tmp/rc.db* /tmp/rcarch
PYTHONPATH=src MBOX_PATH=/tmp/rc.mbox INDEX_PATH=/tmp/rc.db ARCHIVE_DIR=/tmp/rcarch \
  HOST=127.0.0.1 PORT=8137 .venv/bin/python -m mboxviewer.main &
sleep 3
for i in 1 2 3; do curl -s -X POST http://127.0.0.1:8137/api/archive/start >/dev/null; sleep 1; done
curl -s http://127.0.0.1:8137/api/archive/status | \
  .venv/bin/python -c "import sys,json;d=json.load(sys.stdin);print('archived=',d['archived'],'up_to_date=',d['up_to_date'])"
```
Expected: after 3 archive runs the dead image is `gave_up` →
`archived={'ok':0,'skipped':0,'failed':0,'gave_up':1,'total':1}` and `up_to_date=True`.

- [ ] **Step 2: Browser check.** Open http://127.0.0.1:8137 and confirm the footer reads
`… Images: 1 total · 0 archived · 0 skipped · 0 failed · 1 unreachable ✓`. Stop the dev
server + stub; `rm -rf /tmp/rc.mbox /tmp/rc.db* /tmp/rcarch`.

- [ ] **Step 3: Redeploy the real container** (reuses the existing index AND the existing
archive; the `ALTER TABLE` migration adds `attempts` to the live `archive.db` in place).
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Expected: serves in ~seconds (no re-index). The footer initially still shows the 9,777
under `failed` (attempts 0). After ~3 clicks of **Archive remote images**, those settle to
`unreachable` and the footer flips to ✓. Confirm the migration didn't disturb the existing
27,678 archived images (a previously-cached image still renders).

---

## Self-Review Notes

- **Spec coverage:** `attempts` column + guarded migration (Task 1) ✓; `gave_up` terminal
  status + `asset_counts` bucket (Task 1) ✓; `get_attempts` (Task 1) ✓; cap + skip
  `gave_up` + failed→gave_up at `MAX_FETCH_ATTEMPTS` (Task 2) ✓; short-circuit/up_to_date
  unchanged condition now excludes `gave_up` (no code change needed — verified by the
  Task 2 test's 4th-run no-request assertion) ✓; footer `unreachable` segment (Task 3) ✓;
  in-place migration of the live archive (Task 4) ✓.
- **Type/name consistency:** status value `"gave_up"` used identically in archive skip-set,
  the failure branch, `asset_counts`, and the JS `a.gave_up`; `upsert_asset(..., attempts=0)`
  signature matches all call sites (existing 9-arg calls still valid via the default);
  `get_attempts` name consistent; footer reads `s.archived.gave_up` which the API returns
  via `asset_counts()`.
- **No placeholders:** every code/test step is complete; commands include expected output.
```
