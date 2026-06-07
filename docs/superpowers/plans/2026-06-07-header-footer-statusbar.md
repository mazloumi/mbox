# Sticky Header + Footer Status Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the viewer chrome into a sticky top header (logo + Folders/Archive buttons) and a sticky bottom footer that always shows the mbox filename, index state (indexed vs changed), and the persisted archive state.

**Architecture:** Two existing API routes gain additive read-only fields (`/api/status` → `mbox`, `current`; `/api/archive/status` → `archived`, `up_to_date`); the static frontend is restructured into `#header` / `#app` / `#footer` and the pollers write the footer segments.

**Tech Stack:** FastAPI, vanilla JS, CSS flexbox. Tests: pytest + `fastapi.testclient` + a real browser for the layout.

**Spec:** `docs/superpowers/specs/2026-06-07-header-footer-statusbar-design.md`

---

## File Structure

```
src/mboxviewer/api.py            # MODIFY — add fields to /api/status and /api/archive/status
src/mboxviewer/static/index.html # MODIFY — #header + #footer; drop #status-bar/#toolbar
src/mboxviewer/static/app.js     # MODIFY — footer refs; pollStatus/pollArchive write footer
src/mboxviewer/static/style.css  # MODIFY — header/footer bars; drop unused rules
tests/test_api.py                # MODIFY — status field tests
```

---

## Task 1: Backend — status fields for the footer

**Files:** Modify `src/mboxviewer/api.py`; Test `tests/test_api.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_api.py`** (the helper `_client_for_html`, the `image_server` fixture, and `from mboxviewer.archive import ...` are already available in this file/conftest from earlier features):
```python
import os as _os


def test_status_includes_mbox_and_current(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    s = c.get("/api/status").json()
    assert s["mbox"] == _os.path.basename(sample_mbox)
    assert s["current"] is True              # just indexed -> matches the mbox
    _os.utime(sample_mbox, (0, 0))           # change the mbox mtime
    assert c.get("/api/status").json()["current"] is False


def test_archive_status_includes_persisted_state(tmp_path, image_server):
    from mboxviewer.archive import ArchiveStatus, run_archive
    base, _ = image_server
    c, settings = _client_for_html(tmp_path, f'<img src="{base}/logo.png">')
    before = c.get("/api/archive/status").json()
    assert before["archived"]["total"] == 0 and before["up_to_date"] is False
    # run one archive pass synchronously against this app's stores
    run_archive(settings, c.app.state.store, c.app.state.asset_store, ArchiveStatus())
    after = c.get("/api/archive/status").json()
    assert after["archived"]["ok"] == 1 and after["up_to_date"] is True
```

- [ ] **Step 2: Run tests, verify they FAIL** (KeyError on `mbox`/`current`/`archived`/`up_to_date`).
Run: `.venv/bin/pytest tests/test_api.py -k "includes_mbox or persisted" -v`

- [ ] **Step 3: Modify the two routes in `src/mboxviewer/api.py`.**
Replace:
```python
    @app.get("/api/status")
    def get_status():
        return status.snapshot()
```
with:
```python
    @app.get("/api/status")
    def get_status():
        snap = status.snapshot()
        snap["mbox"] = os.path.basename(settings.mbox_path)
        snap["current"] = index_is_current(settings, store)
        return snap
```
Replace:
```python
    @app.get("/api/archive/status")
    def archive_status_route():
        return archive_status.snapshot()
```
with:
```python
    @app.get("/api/archive/status")
    def archive_status_route():
        snap = archive_status.snapshot()
        counts = asset_store.asset_counts()
        meta = asset_store.get_archive_meta()
        try:
            cur_size = os.path.getsize(settings.mbox_path)
            cur_mtime = int(os.path.getmtime(settings.mbox_path))
        except OSError:
            cur_size = cur_mtime = None
        snap["archived"] = counts
        snap["up_to_date"] = bool(
            meta and counts["failed"] == 0
            and meta["source_size"] == cur_size and meta["source_mtime"] == cur_mtime)
        return snap
```
(`os` and `index_is_current` are already imported in api.py; `settings`, `store`,
`asset_store`, `status`, `archive_status` are all in the `create_app` closure.)

- [ ] **Step 4: Run tests, verify they PASS** (and the full api suite).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Run the full suite.**
Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: expose mbox name, index-current, and persisted archive state in status APIs"
```

---

## Task 2: Frontend — header + footer restructure

**Files:** Modify `src/mboxviewer/static/index.html`, `static/app.js`, `static/style.css`.

No unit tests (static assets); verified in Task 3.

- [ ] **Step 1: Replace `src/mboxviewer/static/index.html` entirely with:**
```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>mbox viewer</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header id="header">
    <span id="logo">📬 mbox viewer</span>
    <span id="header-actions">
      <button id="toggle-folders" type="button" title="Show/hide folders">☰ Folders</button>
      <button id="archive-images" type="button" title="Download remote images for offline viewing">Archive remote images</button>
    </span>
  </header>
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
  <footer id="footer">
    <span id="mbox-name"></span>
    <span id="index-state"></span>
    <span id="archive-state"></span>
  </footer>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Update the DOM refs at the top of `src/mboxviewer/static/app.js`.**
Find these two lines:
```javascript
const statusBar = document.getElementById("status-bar");
```
and
```javascript
const archiveStatusEl = document.getElementById("archive-status");
```
Remove both, and add (next to the other `getElementById` refs):
```javascript
const mboxNameEl = document.getElementById("mbox-name");
const indexStateEl = document.getElementById("index-state");
const archiveStateEl = document.getElementById("archive-state");
```
(Keep `toggleFolders`, `appEl`, `archiveBtn`, and all other existing refs.)

- [ ] **Step 3: Replace the entire `pollStatus` function in `app.js`** (keep the `let pollTick = 0;` line that precedes it) with:
```javascript
async function pollStatus() {
  try {
    const s = await getJSON("/api/status");
    if (s.mbox) mboxNameEl.textContent = "📁 " + s.mbox;
    if (s.error) {
      indexStateEl.className = "err";
      indexStateEl.textContent = "Indexing failed: " + s.error;
      return;
    }
    indexStateEl.className = "";
    if (s.indexing) {
      indexStateEl.textContent =
        `Indexing… ${s.percent}% · ${Number(s.messages).toLocaleString()} messages`;
      if (pollTick % 5 === 0) {
        loadLabels();
        if (currentOpenId === null) reload();
      }
      pollTick += 1;
      setTimeout(pollStatus, 2000);
    } else {
      indexStateEl.textContent = s.current
        ? `Indexed ${Number(s.messages).toLocaleString()} messages`
        : "⚠ Source changed — restart to re-index";
      loadLabels();
      if (currentOpenId === null) reload();
    }
  } catch (e) {
    indexStateEl.textContent = "Status unavailable";
    setTimeout(pollStatus, 3000);
  }
}
```

- [ ] **Step 4: Replace the entire `pollArchive` function in `app.js`** with:
```javascript
async function pollArchive() {
  try {
    const s = await getJSON("/api/archive/status");
    const n = (x) => Number(x).toLocaleString();
    if (s.error) {
      archiveStateEl.className = "err";
      archiveStateEl.textContent = "Archive failed: " + s.error;
      archiveBtn.disabled = false;
      return;
    }
    archiveStateEl.className = "";
    if (s.running) {
      archiveBtn.disabled = true;
      archiveStateEl.textContent =
        `Archiving images… ${n(s.messages_scanned)}/${n(s.total_messages)} · ` +
        `${n(s.downloaded)} saved · ${n(s.skipped)} skipped · ${n(s.failed)} failed`;
      setTimeout(pollArchive, 2000);
    } else {
      archiveBtn.disabled = false;
      const a = s.archived || { ok: 0, skipped: 0, failed: 0, total: 0 };
      if (!a.total) {
        archiveStateEl.textContent = "Images: not archived yet";
      } else {
        const breakdown = `Images: ${n(a.total)} total · ${n(a.ok)} archived · ` +
          `${n(a.skipped)} skipped · ${n(a.failed)} failed`;
        archiveStateEl.textContent = breakdown + (s.up_to_date ? " ✓" : " · click to update");
      }
    }
  } catch (e) { /* ignore transient errors */ }
}
```
(The `archiveBtn` click handler — confirm dialog + `POST /api/archive/start` + `pollArchive()` — is unchanged.)

- [ ] **Step 5: Update `src/mboxviewer/static/style.css`.**
Remove the now-unused rules for `#status-bar`, `#status-bar.error`, `#toolbar`, and
`#archive-status` (the `#toolbar` element no longer exists; `#status-bar`/`#archive-status`
are gone). Keep the existing `#toggle-folders` and `#archive-images` rules (those buttons
now live in the header). Then append:
```css
#header { display: flex; align-items: center; justify-content: space-between;
  padding: 5px 12px; border-bottom: 1px solid #ddd; background: #fafafa; }
#logo { font-weight: 600; font-size: 14px; }
#header-actions button { margin-left: 6px; }
#footer { display: flex; padding: 4px 12px; border-top: 1px solid #ddd;
  background: #fafafa; font-size: 12px; color: #555; overflow-x: auto; }
#footer span { white-space: nowrap; }
#footer span:empty { display: none; }
#footer span:not(:last-child)::after { content: "·"; margin: 0 10px; color: #bbb; }
#footer .err { color: #b00020; }
```
(`#footer span:empty { display: none }` hides a segment — and its trailing `·` — until it
has text, avoiding stray separators on first paint. The body is already
`display:flex; flex-direction:column; height:100vh` and `#app` is already
`flex:1; min-height:0`, so the header/footer pin and the panes scroll.)

- [ ] **Step 6: Confirm the backend suite is still green + braces balanced.**
Run: `.venv/bin/pytest -q`  (unchanged from Task 1)
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`

- [ ] **Step 7: Commit.**
```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: sticky header (logo + buttons) and footer status bar"
```

---

## Task 3: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Run the viewer locally against a small mbox with a remote image.**
Start a stub image server + mbox (terminal 1, leave running):
```bash
.venv/bin/python - <<'PY'
import io, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import EmailMessage
from email.generator import BytesGenerator
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body, ctype = b"\x89PNGFAKE", "image/png"
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", 8765), H)  # noqa
srv = ThreadingHTTPServer(("127.0.0.1", 8765), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
html = '<p>Hi</p><img src="http://127.0.0.1:8765/logo.png">'
m = EmailMessage(); m["Subject"]="Pic"; m["From"]="a@x.com"; m["To"]="b@x.com"
m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
m.set_content("b"); m.add_alternative(html, subtype="html")
buf=io.BytesIO(); BytesGenerator(buf).flatten(m); data=buf.getvalue()
open("/tmp/hf.mbox","wb").write(b"From - x\n"+data+(b"" if data.endswith(b"\n") else b"\n")+b"\n")
print("ready"); time.sleep(3600)
PY
```
Terminal 2:
```bash
rm -rf /tmp/hf.db* /tmp/hfarch
PYTHONPATH=src MBOX_PATH=/tmp/hf.mbox INDEX_PATH=/tmp/hf.db ARCHIVE_DIR=/tmp/hfarch \
  HOST=127.0.0.1 PORT=8137 .venv/bin/python -m mboxviewer.main
```

- [ ] **Step 2: Browser check.** Open http://127.0.0.1:8137 and confirm:
  - The **header** is pinned at top with `📬 mbox viewer` and the **☰ Folders** + **Archive remote images** buttons; the buttons still work (folders collapse; archive shows the confirm dialog).
  - The **footer** is pinned at bottom showing `📁 hf.mbox · Indexed 1 messages · Images: not archived yet`.
  - Click **Archive remote images**, confirm — the footer archive segment updates to the
    breakdown `Images: 1 total · 1 archived · 0 skipped · 0 failed ✓`. **Reload the page**
    and confirm the footer still shows that breakdown **without** clicking (persisted state on load).
  - The 3-pane area scrolls between the fixed header and footer.
  Stop the dev server and stub; `rm -rf /tmp/hf.mbox /tmp/hf.db* /tmp/hfarch`.

- [ ] **Step 3: Redeploy the real container.**
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Expected: serves in ~seconds (no re-index; `/api/status` ready ~54k). In the browser at
http://localhost:9000 the header/footer render; the footer shows
`📁 your-mail.mbox · Indexed 54,183 messages · Images: not archived yet` (until
you archive).

---

## Self-Review Notes

- **Spec coverage:** header with logo + Folders/Archive buttons (Task 2 index.html/css) ✓;
  footer with mbox name + index state + archive state (Task 2 pollStatus/pollArchive) ✓;
  `/api/status` `mbox`+`current` and `/api/archive/status` `archived`+`up_to_date`
  (Task 1) ✓; persisted archive state on load (Task 1 `archived`/`up_to_date` + Task 2
  pollArchive idle branch, verified on reload in Task 3) ✓; index "indexed vs changed"
  wording (Task 2) ✓; sticky via flex column (Task 2 css note) ✓.
- **Type/name consistency:** footer element ids (`mbox-name`/`index-state`/`archive-state`)
  match between index.html, the app.js refs, and css; status field names
  (`mbox`,`current`,`archived.{ok,total,failed}`,`up_to_date`,`indexing`,`percent`,`messages`,
  `running`,`messages_scanned`,`total_messages`,`downloaded`,`skipped`,`failed`,`error`)
  match the api routes and the JS readers; `#toggle-folders`/`#archive-images` ids
  unchanged so their existing handlers keep working.
- **No placeholders:** every code/test step is complete; commands have expected output.
```
