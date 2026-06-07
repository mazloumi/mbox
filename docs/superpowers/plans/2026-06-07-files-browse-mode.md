# Files Browse Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Files" browsing mode: the left pane lists file-type categories with counts, clicking a category lists its files, and clicking a file shows its extracted text — all on demand (no re-index).

**Architecture:** A pure `filetypes.category_for_mime` maps each attachment's MIME to a category. Two `Store` queries (mime counts; files by mime set) feed three additive API routes (`/api/filetypes`, `/api/files`, `/api/files/{id}/{idx}/text` — the last reuses the existing reader + extractor). The frontend gains Folders/Files mode tabs that reuse the list and reader panes.

**Tech Stack:** Python 3.9, FastAPI, SQLite, vanilla JS. Tests: pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-06-07-files-browse-mode-design.md`

---

## File Structure

```
src/mboxviewer/filetypes.py    # NEW — category_for_mime(mime) + CATEGORY_ORDER (pure)
src/mboxviewer/store.py        # MODIFY — attachment_mime_counts(), list_files_by_mimes()
src/mboxviewer/api.py          # MODIFY — /api/filetypes, /api/files, /api/files/{id}/{idx}/text
src/mboxviewer/static/index.html # MODIFY — Folders/Files tabs; #reader-text
src/mboxviewer/static/app.js   # MODIFY — mode state, categories, files list, file text
src/mboxviewer/static/style.css # MODIFY — tabs + #reader-text
tests/test_filetypes.py        # NEW
tests/test_store.py            # MODIFY
tests/test_api.py              # MODIFY
```

---

## Task 1: filetypes — MIME → category

**Files:** Create `src/mboxviewer/filetypes.py`; Test `tests/test_filetypes.py`.

- [ ] **Step 1: Write failing tests — `tests/test_filetypes.py`:**
```python
from mboxviewer.filetypes import category_for_mime, CATEGORY_ORDER


def test_categories():
    cases = {
        "application/pdf": "Documents",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Documents",
        "text/plain": "Documents",
        "text/csv": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
        "image/png": "Images",
        "image/jpeg": "Images",
        "application/zip": "Archives",
        "text/calendar": "Calendar",
        "audio/mpeg": "Media",
        "video/mp4": "Media",
        "application/octet-stream": "Other",
        "": "Other",
        None: "Other",
    }
    for mime, expected in cases.items():
        assert category_for_mime(mime) == expected, mime


def test_mime_params_stripped_and_case_insensitive():
    assert category_for_mime("IMAGE/PNG; name=x.png") == "Images"


def test_every_result_is_in_category_order():
    for mime in ["application/pdf", "image/png", "audio/x", "application/zip", "x/y"]:
        assert category_for_mime(mime) in CATEGORY_ORDER
```

- [ ] **Step 2: Run tests, verify they FAIL** (`ModuleNotFoundError`).
Run: `.venv/bin/pytest tests/test_filetypes.py -v`

- [ ] **Step 3: Write `src/mboxviewer/filetypes.py`:**
```python
CATEGORY_ORDER = [
    "Documents", "Spreadsheets", "Presentations", "Images",
    "Archives", "Calendar", "Media", "Other",
]

_DOCUMENTS = {
    "application/pdf", "application/msword", "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
}
_SPREADSHEETS = {
    "application/vnd.ms-excel", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
}
_PRESENTATIONS = {
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
}
_ARCHIVES = {
    "application/zip", "application/x-zip-compressed", "application/gzip",
    "application/x-gzip", "application/x-tar", "application/x-rar-compressed",
    "application/vnd.rar", "application/x-7z-compressed",
}


def category_for_mime(mime):
    m = (mime or "").lower().split(";")[0].strip()
    if m.startswith("image/"):
        return "Images"
    if m.startswith("audio/") or m.startswith("video/"):
        return "Media"
    if m == "text/calendar":
        return "Calendar"
    if m == "text/csv":
        return "Spreadsheets"
    if m in _DOCUMENTS:
        return "Documents"
    if m in _SPREADSHEETS:
        return "Spreadsheets"
    if m in _PRESENTATIONS:
        return "Presentations"
    if m in _ARCHIVES:
        return "Archives"
    if m.startswith("text/"):
        return "Documents"
    return "Other"
```

- [ ] **Step 4: Run tests, verify they PASS.**
Run: `.venv/bin/pytest tests/test_filetypes.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/filetypes.py tests/test_filetypes.py
git commit -m "feat: filetypes.category_for_mime mapping"
```

---

## Task 2: store — attachment mime counts + files-by-mimes

**Files:** Modify `src/mboxviewer/store.py`; Modify `tests/test_store.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_store.py`:**
```python
def test_attachment_mime_counts_and_files(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["mime"]: r["c"] for r in s.attachment_mime_counts()}
    assert counts["application/pdf"] == 1
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert counts[docx] == 1
    files = s.list_files_by_mimes(["application/pdf"], 10, 0)
    assert len(files) == 1
    assert files[0]["filename"] == "invoice.pdf"
    assert files[0]["subject"] == "Welcome aboard" and files[0]["size"] > 0
    assert s.list_files_by_mimes([], 10, 0) == []
```

- [ ] **Step 2: Run test, verify it FAILS** (`AttributeError`).
Run: `.venv/bin/pytest tests/test_store.py::test_attachment_mime_counts_and_files -v`

- [ ] **Step 3: Add to `src/mboxviewer/store.py`** (e.g. after `get_attachments`):
```python
    def attachment_mime_counts(self):
        return self.conn.execute(
            "SELECT mime, COUNT(*) AS c FROM attachments GROUP BY mime").fetchall()

    def list_files_by_mimes(self, mimes, limit, offset):
        mimes = list(mimes)
        if not mimes:
            return []
        placeholders = ",".join("?" * len(mimes))
        return self.conn.execute(
            "SELECT a.message_id AS message_id, a.idx AS idx, a.filename AS filename,"
            " a.size AS size, a.mime AS mime, m.subject AS subject, m.date AS date"
            " FROM attachments a JOIN messages m ON m.id = a.message_id"
            f" WHERE a.mime IN ({placeholders})"
            " ORDER BY a.filename LIMIT ? OFFSET ?",
            (*mimes, limit, offset)).fetchall()
```

- [ ] **Step 4: Run tests, verify they PASS** (and full store suite).
Run: `.venv/bin/pytest tests/test_store.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/store.py tests/test_store.py
git commit -m "feat: store attachment_mime_counts and list_files_by_mimes"
```

---

## Task 3: api — filetypes / files / file-text routes

**Files:** Modify `src/mboxviewer/api.py`; Modify `tests/test_api.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_api.py`:**
```python
def test_filetypes_endpoint(client):
    cats = {c["category"]: c["count"] for c in client.get("/api/filetypes").json()}
    assert cats["Documents"] == 2          # sample has invoice.pdf + report.docx


def test_files_by_category(client):
    data = client.get("/api/files", params={"category": "Documents"}).json()
    names = sorted(f["filename"] for f in data["files"])
    assert names == ["invoice.pdf", "report.docx"]
    assert all("subject" in f and "size" in f and "idx" in f for f in data["files"])


def test_files_unknown_category_empty(client):
    assert client.get("/api/files", params={"category": "Nope"}).json()["files"] == []


def test_file_text_endpoint(client):
    data = client.get("/api/files", params={"category": "Documents"}).json()
    pdf = next(f for f in data["files"] if f["filename"] == "invoice.pdf")
    t = client.get(f"/api/files/{pdf['message_id']}/{pdf['idx']}/text").json()
    assert t["filename"] == "invoice.pdf" and "12345" in t["text"]
    assert client.get("/api/files/999999/0/text").status_code == 404
```

- [ ] **Step 2: Run tests, verify they FAIL** (404 / KeyError).
Run: `.venv/bin/pytest tests/test_api.py -k "filetypes or files or file_text" -v`

- [ ] **Step 3: Modify `src/mboxviewer/api.py`.**
Add imports near the existing ones:
```python
from collections import Counter
from . import filetypes
from .extract import extract_text
```
Add these routes before the `@app.get("/")` route:
```python
    @app.get("/api/filetypes")
    def filetypes_route():
        counter = Counter()
        for r in store.attachment_mime_counts():
            counter[filetypes.category_for_mime(r["mime"])] += r["c"]
        return [{"category": cat, "count": counter[cat]}
                for cat in filetypes.CATEGORY_ORDER if counter[cat]]

    @app.get("/api/files")
    def files(category: str = Query(...),
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        mimes = [r["mime"] for r in store.attachment_mime_counts()
                 if filetypes.category_for_mime(r["mime"]) == category]
        if not mimes:
            return {"files": [], "page": page}
        offset = (page - 1) * page_size
        rows = store.list_files_by_mimes(mimes, page_size, offset)
        return {"files": [{"message_id": r["message_id"], "idx": r["idx"],
                           "filename": r["filename"], "size": r["size"], "mime": r["mime"],
                           "subject": r["subject"], "date": r["date"]} for r in rows],
                "page": page}

    @app.get("/api/files/{message_id}/{idx}/text")
    def file_text(message_id: int, idx: int):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return {"filename": filename, "mime": mime, "size": len(payload),
                        "text": extract_text(filename, mime, payload)}
        raise HTTPException(404, "attachment not found")
```

- [ ] **Step 4: Run tests, verify they PASS** (the new ones + the full api suite).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Run the full suite.**
Run: `.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: /api/filetypes, /api/files, and /api/files/{id}/{idx}/text routes"
```

---

## Task 4: Frontend — Folders/Files tabs + file browsing

**Files:** Modify `src/mboxviewer/static/index.html`, `static/app.js`, `static/style.css`.

No unit tests (static assets); verified in Task 5.

- [ ] **Step 1: `index.html` — replace the single Folders button with two tabs, and add the file-text element.**
Replace:
```html
    <button id="toggle-folders" type="button" title="Show/hide folders">☰ Folders</button>
```
with:
```html
    <button id="tab-folders" type="button" class="tab active" title="Browse Gmail labels">Folders</button>
    <button id="tab-files" type="button" class="tab" title="Browse attachments by type">Files</button>
```
And in the `#reader` section, add `<pre id="reader-text" hidden></pre>` immediately after the `<iframe id="reader-pdf" hidden></iframe>` line.

- [ ] **Step 2: `app.js` — update the DOM refs.**
Find and REMOVE:
```javascript
const toggleFolders = document.getElementById("toggle-folders");
```
Add (next to the other refs):
```javascript
const tabFolders = document.getElementById("tab-folders");
const tabFiles = document.getElementById("tab-files");
const readerText = document.getElementById("reader-text");
const searchbar = document.getElementById("searchbar");
```
Add these state variables next to `let activeLabel = null;`:
```javascript
let browseMode = "folders";   // "folders" | "files"
let activeCategory = null;
```

- [ ] **Step 3: `app.js` — add helpers `humanSize`, `refreshLeft`, `loadCategories` (place near `loadLabels`):**
```javascript
function humanSize(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}

function refreshLeft() {
  if (browseMode === "files") loadCategories();
  else loadLabels();
}

async function loadCategories() {
  try {
    const cats = await getJSON("/api/filetypes");
    labelList.innerHTML = "";
    for (const c of cats) {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(c.category)}<span class="count">${escapeHtml(String(c.count))}</span>`;
      li.onclick = () => { activeCategory = c.category; setActive(labelList, li); reload(); };
      labelList.appendChild(li);
    }
  } catch (err) {
    labelList.innerHTML = `<li>Failed to load file types: ${escapeHtml(String(err.message))}</li>`;
  }
}
```

- [ ] **Step 4: `app.js` — make `pageUrl` and the list rendering mode-aware.**
Replace the existing `pageUrl` function with:
```javascript
function pageUrl(page) {
  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (browseMode === "files") {
    params.set("category", activeCategory || "");
    return `/api/files?${params.toString()}`;
  }
  if (activeLabel) params.set("label", activeLabel);
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
  return `/api/messages?${params.toString()}`;
}
```
Replace the existing `appendMessages` function with a mode-aware `appendRows` (and update its callers in the next step):
```javascript
function appendRows(items) {
  for (const it of items) {
    const li = document.createElement("li");
    if (browseMode === "files") {
      li.innerHTML = `<div class="subject">${escapeHtml(it.filename || "(no name)")}</div>
        <div class="meta">${escapeHtml(it.subject || "")} — ${humanSize(it.size)}</div>`;
      li.onclick = () => { setActive(messageList, li); openFile(it.message_id, it.idx, it.filename, it.mime, it.size); };
    } else {
      li.innerHTML = `<div class="subject">${escapeHtml(it.subject || "(no subject)")}</div>
        <div class="meta">${escapeHtml(it.from || "")} — ${escapeHtml((it.date || "").slice(0, 10))}</div>`;
      li.onclick = () => { setActive(messageList, li); openMessage(it.id); };
    }
    messageList.appendChild(li);
  }
  renderLoadMore(items.length);
}
```

- [ ] **Step 5: `app.js` — update `reload` and `loadNextPage` to use `appendRows` and the files guard.**
Replace `reload` with:
```javascript
async function reload() {
  currentPage = 1;
  messageList.innerHTML = "";
  if (browseMode === "files" && !activeCategory) return;  // pick a category first
  try {
    const data = await getJSON(pageUrl(1));
    appendRows(data.messages || data.files || []);
  } catch (err) {
    messageList.innerHTML = `<li>Failed to load: ${escapeHtml(String(err.message))}</li>`;
  }
}
```
Replace `loadNextPage` with:
```javascript
async function loadNextPage() {
  currentPage += 1;
  try {
    const data = await getJSON(pageUrl(currentPage));
    appendRows(data.messages || data.files || []);
  } catch (err) {
    renderLoadMore(0);
  }
}
```

- [ ] **Step 6: `app.js` — add `openFile`, and hide the text pane inside `openMessage`.**
Add `openFile` (near `openMessage`):
```javascript
async function openFile(mid, idx, filename, mime, size) {
  currentOpenId = mid;
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerBody.hidden = true; readerBody.srcdoc = "";
  readerText.hidden = false;
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(filename || "(no name)")}</div>
    <div class="meta">${escapeHtml(mime || "")} · ${humanSize(size)}</div>`;
  readerAtt.innerHTML = `<a href="/api/messages/${mid}/attachments/${idx}" download>Download</a>`;
  readerText.textContent = "Loading…";
  try {
    const d = await getJSON(`/api/files/${mid}/${idx}/text`);
    readerText.textContent = (d.text && d.text.trim())
      ? d.text : "No extractable text for this file type.";
  } catch (err) {
    readerText.textContent = "Failed to load file text: " + err.message;
  }
}
```
In `openMessage`, find its opening lines:
```javascript
async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  readerBody.hidden = false;
```
and add `readerText.hidden = true;` so it becomes:
```javascript
async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  readerBody.hidden = false;
  readerText.hidden = true;
```

- [ ] **Step 7: `app.js` — replace the folder-collapse block with `setMode`, `toggleCollapse`, and the tab handlers.**
Find the existing collapse block (the `applyFoldersCollapsed` function, the `toggleFolders` click listener, and the `localStorage.getItem("foldersCollapsed")` apply-on-load line) and REPLACE all of it with:
```javascript
// --- Folders/Files mode tabs + collapse (persisted) ---
function toggleCollapse() {
  const collapsed = !appEl.classList.contains("folders-collapsed");
  appEl.classList.toggle("folders-collapsed", collapsed);
  try { localStorage.setItem("foldersCollapsed", collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
}

function setMode(mode) {
  browseMode = mode;
  currentOpenId = null;
  activeCategory = null;
  tabFolders.classList.toggle("active", mode === "folders");
  tabFiles.classList.toggle("active", mode === "files");
  appEl.classList.remove("folders-collapsed");
  searchbar.style.display = (mode === "files") ? "none" : "";
  readerHeader.innerHTML = "";
  readerAtt.innerHTML = "";
  readerBody.srcdoc = ""; readerBody.hidden = (mode === "files");
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerText.hidden = true; readerText.textContent = "";
  refreshLeft();
  reload();
}

tabFolders.addEventListener("click", () => {
  if (browseMode !== "folders") setMode("folders"); else toggleCollapse();
});
tabFiles.addEventListener("click", () => {
  if (browseMode !== "files") setMode("files"); else toggleCollapse();
});
try {
  if (localStorage.getItem("foldersCollapsed") === "1") appEl.classList.add("folders-collapsed");
} catch (e) { /* ignore */ }
```

- [ ] **Step 8: `app.js` — make `pollStatus` refresh the correct left list.**
In `pollStatus`, replace BOTH occurrences of `loadLabels();` with `refreshLeft();` (there are two — one in the `s.indexing` branch's `pollTick % 5` block, one in the ready `else` branch).

- [ ] **Step 9: `style.css` — add tab + reader-text styles. Append:**
```css
.tab { font-size: 12px; line-height: 1.4; padding: 2px 10px; margin-left: 6px; cursor: pointer;
  background: #f3f3f3; border: 1px solid #ccc; border-radius: 4px; }
.tab.active { background: #e7efff; border-color: #9bb8e8; font-weight: 600; }
#reader-text { flex: 1; margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap;
  word-break: break-word; font: 13px/1.5 ui-monospace, Menlo, Consolas, monospace; border-top: 1px solid #ddd; }```

- [ ] **Step 10: Verify the backend suite is green + braces balanced + no stale refs.**
Run: `.venv/bin/pytest -q`  (unchanged from Task 3)
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `grep -n "toggle-folders\|toggleFolders\|appendMessages" src/mboxviewer/static/*.js src/mboxviewer/static/*.html` — should return NOTHING (the old button id, ref, and function are fully gone). Report what it shows.

- [ ] **Step 11: Commit.**
```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: Folders/Files mode tabs and file-text browsing in the frontend"
```

---

## Task 5: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Run the viewer locally against the sample-style mbox (a PDF + a DOCX).**
Terminal 1 (write a small mbox with two document attachments, leave the server running):
```bash
rm -rf /tmp/fb.db* /tmp/fbarch
.venv/bin/python - <<'PY'
import io
from email.message import EmailMessage
from email.generator import BytesGenerator
from reportlab.pdfgen import canvas
import docx
def pdf(t):
    b=io.BytesIO(); c=canvas.Canvas(b); c.drawString(72,720,t); c.save(); return b.getvalue()
def dx(t):
    b=io.BytesIO(); d=docx.Document(); d.add_paragraph(t); d.save(b); return b.getvalue()
def email(sub, atts):
    m=EmailMessage(); m["Subject"]=sub; m["From"]="a@x.com"; m["To"]="b@x.com"
    m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
    m.set_content("body"); m.add_alternative("<p>see attached</p>", subtype="html")
    for fn,mt,st,data in atts: m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    return m
msgs=[email("Invoice", [("invoice.pdf","application","pdf",pdf("INVOICE 12345 ACME"))]),
      email("Report", [("q1.docx","application","vnd.openxmlformats-officedocument.wordprocessingml.document",dx("QUARTERLY REPORT GROWTH"))])]
with open("/tmp/fb.mbox","wb") as f:
    for m in msgs:
        data=io.BytesIO(); BytesGenerator(data).flatten(m); data=data.getvalue()
        f.write(b"From - x\n"+data+(b"" if data.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/fb.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/fb.mbox INDEX_PATH=/tmp/fb.db ARCHIVE_DIR=/tmp/fbarch \
  HOST=127.0.0.1 PORT=8137 .venv/bin/python -m mboxviewer.main
```
Terminal 2 — API smoke test:
```bash
curl -s http://127.0.0.1:8137/api/filetypes              # [{"category":"Documents","count":2}]
curl -s "http://127.0.0.1:8137/api/files?category=Documents" | .venv/bin/python -m json.tool
MID=$(curl -s "http://127.0.0.1:8137/api/files?category=Documents" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])")
curl -s "http://127.0.0.1:8137/api/files/${MID/ //}/text"  # falls through to browser check below
```

- [ ] **Step 2: Browser check.** Open http://127.0.0.1:8137 and confirm:
  - Header shows **Folders** and **Files** tabs (Folders active). Click **Files** → the left
    pane shows `Documents  2` (and any other categories). The search box hides.
  - Click **Documents** → the middle pane lists `invoice.pdf` and `q1.docx` (with subject + size).
  - Click `invoice.pdf` → the reader shows its extracted text containing `INVOICE 12345 ACME`,
    a header with the filename · type · size, and a **Download** link that downloads the PDF.
  - Click **Files** again (active tab) → the left pane collapses; click again → expands.
  - Click **Folders** → returns to labels + messages; the search box reappears.
  Stop the server; `rm -rf /tmp/fb.mbox /tmp/fb.db* /tmp/fbarch`.

- [ ] **Step 3: Redeploy the real container** (no schema/migration changes; reuses the existing index).
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Expected: serves in ~seconds (no re-index). In the browser at http://localhost:9000, click
**Files** → real categories with counts (Documents ~3,929, Images, etc.); click a category →
files; click a PDF → its extracted text + Download.

---

## Self-Review Notes

- **Spec coverage:** `category_for_mime` + `CATEGORY_ORDER` (Task 1) ✓; mime-only counts &
  files-by-mimes (Task 2) ✓; `/api/filetypes`, `/api/files`, `/api/files/{id}/{idx}/text`
  reusing reader+extractor (Task 3) ✓; Folders/Files tabs + active highlight + collapse on
  active-tab (Task 4 steps 1,7) ✓; left pane categories, middle file list w/ subject+size,
  reader extracted-text + Download + "no text" fallback (Task 4 steps 3-6) ✓; search hidden
  in Files mode (Task 4 step 7) ✓; arrow-nav reuses row click (unchanged; appendRows sets
  onclick) ✓; no re-index (Task 5) ✓.
- **Type/name consistency:** field names returned by the API (`category`,`count`;
  `files[].{message_id,idx,filename,size,mime,subject,date}`; file-text `{filename,mime,size,text}`)
  match the JS readers; `browseMode`/`activeCategory`/`appendRows`/`refreshLeft`/`openFile`/
  `setMode`/`toggleCollapse` used consistently; element ids (`tab-folders`,`tab-files`,
  `reader-text`,`searchbar`) match index.html ↔ app.js ↔ css; `attachment_mime_counts`/
  `list_files_by_mimes` signatures match the api call sites; the removed `toggle-folders`/
  `toggleFolders`/`appendMessages` are replaced everywhere (Task 4 step 10 grep).
- **No placeholders:** every code/test step is complete; commands include expected output.
