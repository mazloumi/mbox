# Files Mode Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an email link, filename+content search, image preview, and pptx/xlsx/xls text extraction to the existing Files browse mode.

**Architecture:** `extract.py` gains three MIME-dispatched extractors. `store.list_files_by_mimes` gains an optional `query` that filters by filename `LIKE` OR the existing per-message FTS. `GET /api/files` makes `category`/`q` optional. The frontend shows the search box in Files mode, previews images inline (reusing the allowlisted `?inline=1` endpoint), and adds an "Open email" action that switches to Folders mode.

**Tech Stack:** Python 3.9, FastAPI, SQLite FTS5, vanilla JS. New deps: `python-pptx`, `openpyxl`, `xlrd` (runtime); `xlwt` (dev/test). Tests: pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-06-07-files-mode-enhancements-design.md`

---

## File Structure

```
src/mboxviewer/extract.py        # MODIFY — _pptx_text/_xlsx_text/_xls_text + dispatch
src/mboxviewer/store.py          # MODIFY — list_files_by_mimes(query=...)
src/mboxviewer/api.py            # MODIFY — /api/files optional category + q
src/mboxviewer/static/index.html # MODIFY — #reader-image
src/mboxviewer/static/app.js     # MODIFY — files search, image preview, open-email
src/mboxviewer/static/style.css  # MODIFY — #reader-image
requirements.txt                 # MODIFY — python-pptx, openpyxl, xlrd
requirements-dev.txt             # MODIFY — xlwt (test-only)
tests/test_extract.py            # MODIFY — pptx/xlsx/xls round-trips
tests/test_store.py              # MODIFY — file search
tests/test_api.py                # MODIFY — /api/files q + optional category
```

---

## Task 1: extract — pptx / xlsx / xls text extraction

**Files:** Modify `src/mboxviewer/extract.py`, `requirements.txt`, `requirements-dev.txt`; Test `tests/test_extract.py`.

- [ ] **Step 1: Add deps.**
Append to `requirements.txt`:
```
python-pptx==1.0.2
openpyxl==3.1.5
xlrd==2.0.1
```
Append to `requirements-dev.txt`:
```
xlwt==1.3.0
```
Then install into the dev venv:
Run: `.venv/bin/pip install python-pptx==1.0.2 openpyxl==3.1.5 xlrd==2.0.1 xlwt==1.3.0`
Expected: installs succeed.

- [ ] **Step 2: Write failing tests — add to `tests/test_extract.py`:**
```python
def test_extract_pptx():
    import io
    from pptx import Presentation
    from pptx.util import Inches
    from mboxviewer.extract import extract_text
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = "ROADMAP Q3 LAUNCH"
    buf = io.BytesIO(); prs.save(buf)
    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert "ROADMAP Q3 LAUNCH" in extract_text("d.pptx", mime, buf.getvalue())


def test_extract_xlsx():
    import io
    import openpyxl
    from mboxviewer.extract import extract_text
    wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = "Region"; ws["B1"] = "Sales"; ws["A2"] = "EMEA"; ws["B2"] = 4200
    buf = io.BytesIO(); wb.save(buf)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    text = extract_text("d.xlsx", mime, buf.getvalue())
    assert "EMEA" in text and "4200" in text


def test_extract_xls():
    import io
    import xlwt
    from mboxviewer.extract import extract_text
    wb = xlwt.Workbook(); ws = wb.add_sheet("S1")
    ws.write(0, 0, "Account"); ws.write(0, 1, "Balance"); ws.write(1, 0, "ACME"); ws.write(1, 1, 999)
    buf = io.BytesIO(); wb.save(buf)
    text = extract_text("d.xls", "application/vnd.ms-excel", buf.getvalue())
    assert "ACME" in text and "999" in text


def test_extract_corrupt_office_returns_empty():
    from mboxviewer.extract import extract_text
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert extract_text("d.xlsx", mime, b"not a real zip") == ""
```

- [ ] **Step 3: Run tests, verify they FAIL** (no extraction yet → assertion fails / returns "").
Run: `.venv/bin/pytest tests/test_extract.py -k "pptx or xlsx or xls or corrupt" -v`

- [ ] **Step 4: Implement in `src/mboxviewer/extract.py`.**
Add MIME constants near `_DOCX_MIME`:
```python
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_XLS_MIME = "application/vnd.ms-excel"
```
Add helpers (near `_docx_text`):
```python
def _pptx_text(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(p for p in parts if p)


def _rows_text(rows) -> str:
    out = []
    for row in rows:
        cells = [str(c) for c in row if c is not None and str(c).strip() != ""]
        if cells:
            out.append("\t".join(cells))
    return "\n".join(out)


def _xlsx_text(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return "\n".join(_rows_text(ws.iter_rows(values_only=True)) for ws in wb.worksheets)
    finally:
        wb.close()


def _xls_text(data: bytes) -> str:
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    sheets = []
    for sheet in book.sheets():
        rows = (sheet.row_values(r) for r in range(sheet.nrows))
        sheets.append(_rows_text(rows))
    return "\n".join(sheets)
```
Add dispatch branches inside `extract_text`'s `try` (after the `_DOCX_MIME` branch, before the `text/html` branch):
```python
        if mime == _PPTX_MIME:
            return _pptx_text(data)
        if mime == _XLSX_MIME:
            return _xlsx_text(data)
        if mime == _XLS_MIME:
            return _xls_text(data)
```

- [ ] **Step 5: Run tests, verify they PASS** (and the full extract suite).
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/extract.py requirements.txt requirements-dev.txt tests/test_extract.py
git commit -m "feat: extract text from pptx, xlsx, and xls attachments"
```

---

## Task 2: store — filename + content search for files

**Files:** Modify `src/mboxviewer/store.py`; Modify `tests/test_store.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_store.py`:**
```python
def test_list_files_by_mimes_search(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    pdf = "application/pdf"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    allmimes = [pdf, docx]
    # filename match
    by_name = s.list_files_by_mimes(allmimes, 50, 0, query="invoice")
    assert [f["filename"] for f in by_name] == ["invoice.pdf"]
    # content match: a term inside the indexed PDF text (see conftest sample body/attachment)
    by_content = s.list_files_by_mimes([pdf], 50, 0, query="12345")
    assert any(f["filename"] == "invoice.pdf" for f in by_content)
    # no-mime + query searches across all files
    cross = s.list_files_by_mimes([], 50, 0, query="invoice")
    assert any(f["filename"] == "invoice.pdf" for f in cross)
    # garbage query (no usable FTS terms) falls back to filename LIKE → no crash
    assert s.list_files_by_mimes(allmimes, 50, 0, query="!!!") == []
    # no mimes and no query → empty
    assert s.list_files_by_mimes([], 50, 0, query=None) == []
```
NOTE: `"12345"` is verified present — `tests/conftest.py` builds the sample PDF with text
`"INVOICE 12345"`, which the indexer folds into `messages_fts`. Use it as-is.

- [ ] **Step 2: Run test, verify it FAILS** (`TypeError: unexpected keyword 'query'`).
Run: `.venv/bin/pytest tests/test_store.py::test_list_files_by_mimes_search -v`

- [ ] **Step 3: Replace `list_files_by_mimes` in `src/mboxviewer/store.py`** with:
```python
    def list_files_by_mimes(self, mimes, limit, offset, query=None):
        # Drop NULLs: SQLite's `IN (NULL)` silently matches nothing, which would
        # produce a degenerate query rather than an honest empty result.
        mimes = [m for m in mimes if m is not None]
        where = []
        params = []
        if mimes:
            placeholders = ",".join("?" * len(mimes))
            where.append(f"a.mime IN ({placeholders})")
            params.extend(mimes)
        q = (query or "").strip()
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            match = _fts_query(q)
            if match:
                where.append("(a.filename LIKE ? ESCAPE '\\' OR a.message_id IN"
                             " (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?))")
                params.extend([like, match])
            else:
                where.append("a.filename LIKE ? ESCAPE '\\'")
                params.append(like)
        if not where:
            return []
        sql = ("SELECT a.message_id AS message_id, a.idx AS idx, a.filename AS filename,"
               " a.size AS size, a.mime AS mime, m.subject AS subject, m.date AS date"
               " FROM attachments a JOIN messages m ON m.id = a.message_id"
               f" WHERE {' AND '.join(where)}"
               " ORDER BY a.filename LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such table" in message or "fts5" in message or "syntax error" in message:
                return []
            raise
```

- [ ] **Step 4: Run tests, verify they PASS** (and the full store suite).
Run: `.venv/bin/pytest tests/test_store.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/store.py tests/test_store.py
git commit -m "feat: filename + content search in list_files_by_mimes"
```

---

## Task 3: api — /api/files optional category + q

**Files:** Modify `src/mboxviewer/api.py`; Modify `tests/test_api.py`.

- [ ] **Step 1: Write failing tests — add to `tests/test_api.py`:**
```python
def test_files_search_within_category(client):
    data = client.get("/api/files", params={"category": "Documents", "q": "invoice"}).json()
    assert [f["filename"] for f in data["files"]] == ["invoice.pdf"]


def test_files_search_no_category(client):
    data = client.get("/api/files", params={"q": "invoice"}).json()
    assert any(f["filename"] == "invoice.pdf" for f in data["files"])


def test_files_no_category_no_query_empty(client):
    assert client.get("/api/files").json()["files"] == []
```

- [ ] **Step 2: Run tests, verify they FAIL** (422 for missing required `category`, or wrong results).
Run: `.venv/bin/pytest tests/test_api.py -k "files_search or no_category_no_query" -v`

- [ ] **Step 3: Replace the `/api/files` route in `src/mboxviewer/api.py`** with:
```python
    @app.get("/api/files")
    def files(category: Optional[str] = None, q: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        query = (q or "").strip()
        mimes = []
        if category:
            mimes = [r["mime"] for r in store.attachment_mime_counts()
                     if filetypes.category_for_mime(r["mime"]) == category]
            if not mimes:
                return {"files": [], "page": page}
        elif not query:
            return {"files": [], "page": page}
        offset = (page - 1) * page_size
        rows = store.list_files_by_mimes(mimes, page_size, offset, query=query or None)
        return {"files": [{"message_id": r["message_id"], "idx": r["idx"],
                           "filename": r["filename"], "size": r["size"], "mime": r["mime"],
                           "subject": r["subject"], "date": r["date"]} for r in rows],
                "page": page}
```

- [ ] **Step 4: Run tests, verify they PASS** (new + the full api suite — the existing
  `test_files_by_category` and `test_files_unknown_category_empty` must still pass).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Run the full suite.**
Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: /api/files optional category and q (filename + content search)"
```

---

## Task 4: Frontend — files search, image preview, open-email

**Files:** Modify `src/mboxviewer/static/index.html`, `static/app.js`, `static/style.css`.
No unit tests (static assets); verified in Task 5.

- [ ] **Step 1: `index.html` — add the image preview element.**
Find `<pre id="reader-text" hidden></pre>` and add immediately after it:
```html
      <img id="reader-image" hidden>
```

- [ ] **Step 2: `app.js` — add the DOM ref.** Next to `const readerText = ...`:
```javascript
const readerImage = document.getElementById("reader-image");
```

- [ ] **Step 3: `app.js` — make `pageUrl`'s files branch include `q`, and relax the `reload` guard.**
Replace the `if (browseMode === "files") { ... }` block inside `pageUrl` with:
```javascript
  if (browseMode === "files") {
    if (activeCategory) params.set("category", activeCategory);
    if (currentQuery) params.set("q", currentQuery);
    return `/api/files?${params.toString()}`;
  }
```
In `reload`, replace the guard line:
```javascript
  if (browseMode === "files" && !activeCategory) return;  // pick a category first
```
with:
```javascript
  if (browseMode === "files" && !activeCategory && !currentQuery) return;  // need a category or a query
```

- [ ] **Step 4: `app.js` — image preview + open-email in `openFile`.**
Replace the whole `openFile` function with:
```javascript
async function openFile(mid, idx, filename, mime, size) {
  currentOpenId = mid;
  readerBody.hidden = true; readerBody.srcdoc = "";
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(filename || "(no name)")}</div>
    <div class="meta">${escapeHtml(mime || "")} · ${humanSize(size)}</div>`;
  readerAtt.innerHTML =
    `<a href="/api/messages/${mid}/attachments/${idx}" download>Download</a>` +
    ` <button type="button" class="open-email" onclick="openEmailFromFile(${mid})">Open email</button>`;
  if ((mime || "").toLowerCase().startsWith("image/")) {
    readerText.hidden = true; readerText.textContent = "";
    readerImage.src = `/api/messages/${mid}/attachments/${idx}?inline=1`;
    readerImage.hidden = false;
    return;
  }
  readerImage.hidden = true; readerImage.removeAttribute("src");
  readerText.hidden = false;
  readerText.textContent = "Loading…";
  try {
    const d = await getJSON(`/api/files/${mid}/${idx}/text`);
    readerText.textContent = (d.text && d.text.trim())
      ? d.text : "No extractable text for this file type.";
  } catch (err) {
    readerText.textContent = "Failed to load file text: " + err.message;
  }
}

function openEmailFromFile(mid) {
  setMode("folders");
  openMessage(mid);
}
```

- [ ] **Step 5: `app.js` — hide `#reader-image` everywhere the other exclusive panes are hidden.**
In `openMessage`, after the existing `readerText.hidden = true;` line, add:
```javascript
  readerImage.hidden = true; readerImage.removeAttribute("src");
```
In `viewPdf`, after `readerText.hidden = true;`, add:
```javascript
  readerImage.hidden = true; readerImage.removeAttribute("src");
```

- [ ] **Step 6: `app.js` — show the search box in Files mode and reset the query on mode switch.**
In `setMode`, REMOVE the line:
```javascript
  searchbar.style.display = (mode === "files") ? "none" : "";
```
and ADD (right after `tabFiles.classList.toggle("active", mode === "files");`):
```javascript
  currentQuery = "";
  q.value = "";
```
Also in `setMode`, in the reader-reset section add image hiding next to the text reset:
```javascript
  readerImage.hidden = true; readerImage.removeAttribute("src");
```

- [ ] **Step 7: `style.css` — append the image-preview style.**
```css
#reader-image { flex: 1; min-height: 0; max-width: 100%; object-fit: contain;
  margin: 0; padding: 12px; align-self: center; }
```

- [ ] **Step 8: Verify braces balanced + no stale refs + search box no longer force-hidden.**
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `grep -n "searchbar.style.display" src/mboxviewer/static/app.js` — should return NOTHING.
Run: `grep -n "reader-image\|readerImage" src/mboxviewer/static/index.html src/mboxviewer/static/app.js` — should show the new element + refs. Report what it shows.

- [ ] **Step 9: Commit.**
```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: files search box, inline image preview, and open-email link"
```

---

## Task 5: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Build a sample mbox with a PDF, an XLSX, and a PNG image, and run the viewer.**
Terminal 1:
```bash
rm -rf /tmp/fe.db* /tmp/fearch /tmp/fe.mbox 2>/dev/null
.venv/bin/python - <<'PY'
import io
from email.message import EmailMessage
from email.generator import BytesGenerator
from reportlab.pdfgen import canvas
import openpyxl
def pdf(t):
    b=io.BytesIO(); c=canvas.Canvas(b); c.drawString(72,720,t); c.save(); return b.getvalue()
def xlsx(rows):
    b=io.BytesIO(); wb=openpyxl.Workbook(); ws=wb.active
    for r in rows: ws.append(r)
    wb.save(b); return b.getvalue()
def png():
    # 1x1 red PNG
    import base64
    return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
def email(sub, atts):
    m=EmailMessage(); m["Subject"]=sub; m["From"]="a@x.com"; m["To"]="b@x.com"
    m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
    m.set_content("body"); m.add_alternative("<p>see attached</p>", subtype="html")
    for fn,mt,st,data in atts: m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    return m
XLSX="vnd.openxmlformats-officedocument.spreadsheetml.sheet"
msgs=[email("Invoice", [("invoice.pdf","application","pdf",pdf("INVOICE 12345 ACME"))]),
      email("Numbers", [("data.xlsx","application",XLSX,xlsx([["Region","Sales"],["EMEA",4200]]))]),
      email("Picture", [("photo.png","image","png",png())])]
with open("/tmp/fe.mbox","wb") as f:
    for m in msgs:
        d=io.BytesIO(); BytesGenerator(d).flatten(m); d=d.getvalue()
        f.write(b"From - x\n"+d+(b"" if d.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/fe.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/fe.mbox INDEX_PATH=/tmp/fe.db ARCHIVE_DIR=/tmp/fearch \
  HOST=127.0.0.1 PORT=8138 .venv/bin/python -m mboxviewer.main
```
Terminal 2 — API smoke test:
```bash
curl -s "http://127.0.0.1:8138/api/files?q=invoice"                       # invoice.pdf
curl -s "http://127.0.0.1:8138/api/files?category=Spreadsheets&q=data"     # data.xlsx
# xlsx extracted text:
curl -s "http://127.0.0.1:8138/api/files?category=Spreadsheets" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])" | { read M I; curl -s "http://127.0.0.1:8138/api/files/$M/$I/text"; }  # contains EMEA / 4200
```

- [ ] **Step 2: Browser check** at http://127.0.0.1:8138:
  - Files mode: the **search box is visible**. Type `invoice` → the file list filters to `invoice.pdf`.
  - Click **Images** → `photo.png`; click it → an **inline image preview** in the reader (not text),
    with **Download** + **Open email**.
  - Click **Open email** → switches to **Folders** mode and opens the "Picture" message.
  - Click **Spreadsheets** → `data.xlsx`; click it → extracted text containing `EMEA` and `4200`.
  Stop the server; `rm -rf /tmp/fe.mbox /tmp/fe.db* /tmp/fearch`.

- [ ] **Step 3: Redeploy the real container.** New Python deps are in `requirements.txt`, so the
  image rebuilds; the existing index is reused (mbox unchanged → no re-index).
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Then verify at http://localhost:9000: Files mode search filters files; an Images file previews
inline with a working **Open email** link; a `.xlsx` shows extracted text. Confirm the status bar
still reports the indexed message count (no re-index).
NOTE: content search for `pptx`/`xlsx`/`xls` reflects only the *previously* indexed text until a
re-index; filename search and the on-demand reader work immediately (documented in the spec).

---

## Self-Review Notes

- **Spec coverage:** pptx/xlsx/xls extraction (Task 1) ✓; filename+content file search reusing
  `messages_fts` (Task 2) ✓; `/api/files` optional `category`+`q` (Task 3) ✓; search box in Files
  mode, inline image preview via `?inline=1`, Open-email switching to Folders (Task 4) ✓; e2e +
  redeploy + FTS note (Task 5) ✓.
- **Type/name consistency:** `list_files_by_mimes(mimes, limit, offset, query=None)` signature
  matches the api call site; `/api/files` returns the same field set the JS `appendRows`/`openFile`
  read; new refs `readerImage`/`openEmailFromFile` used consistently; `#reader-image` id matches
  index.html ↔ app.js ↔ css; the four reader panes (body/pdf/text/image) are hidden/shown
  exclusively across `openMessage`/`viewPdf`/`openFile`/`setMode`.
- **No placeholders:** every step has complete code/commands; the one conditional (content-match
  test term) tells the implementer to verify against `tests/conftest.py` and adjust.
