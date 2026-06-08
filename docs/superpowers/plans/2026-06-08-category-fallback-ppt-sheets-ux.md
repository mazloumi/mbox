# Category Fallback / Legacy PPT / Spreadsheet Table / Sticky Search / WMA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the over-stuffed "Other" category via filename-extension fallback (stored category column), add legacy `.ppt`/`.pps` text extraction, render spreadsheets as a table, pin the search box, and show a clear message for un-playable WMA/WMV.

**Architecture:** Categorization becomes `category_for(mime, filename)` (MIME first, extension fallback when MIME→Other), computed at index time and stored in a new `attachments.category` column; `/api/filetypes` and `/api/files` query that column. `.ppt` text via `catppt`. Frontend gains a spreadsheet→table branch, a sticky search box, and a WMA/WMV notice.

**Tech Stack:** Python 3.9, FastAPI, SQLite, vanilla JS. New: `catdoc` Debian package (provides `catppt`). Tests: pytest + testclient. **Requires a re-index** (schema + extraction change).

**Spec:** `docs/superpowers/specs/2026-06-08-category-fallback-ppt-sheets-ux-design.md`

---

## File Structure
```
src/mboxviewer/filetypes.py      # MODIFY — category_for(mime, filename) + _EXT_CATEGORY
src/mboxviewer/store.py          # MODIFY — category column + migration + category counts/listing
src/mboxviewer/indexer.py        # MODIFY — compute + store category
src/mboxviewer/api.py            # MODIFY — filetypes/files use category
src/mboxviewer/extract.py        # MODIFY — _ppt_text (catppt)
Dockerfile                       # MODIFY — catdoc
src/mboxviewer/static/app.js     # MODIFY — spreadsheet table, WMA message
src/mboxviewer/static/style.css  # MODIFY — sticky searchbar
tests/test_filetypes.py, test_store.py, test_api.py, test_extract.py  # MODIFY
```

---

## Task 1: filetypes — extension fallback

**Files:** Modify `src/mboxviewer/filetypes.py`; Modify `tests/test_filetypes.py`.

- [ ] **Step 1: Failing tests — add to `tests/test_filetypes.py`:**
```python
def test_category_for_extension_fallback():
    from mboxviewer.filetypes import category_for
    cases = {
        ("application/octet-stream", "x.pdf"): "Documents",
        ("application/x-pdf", "x.pdf"): "Documents",
        ("application/force-download", "report.doc"): "Documents",
        ("application/octet-stream", "s.xlsx"): "Spreadsheets",
        ("application/octet-stream", "d.pps"): "Presentations",
        ("application/octet-stream", "a.mp3"): "Media",
        ("application/octet-stream", "v.wmv"): "Media",
        ("application/octet-stream", "c.ics"): "Calendar",
        ("application/octet-stream", "k.vcf"): "Contacts",
        ("application/octet-stream", "p.jpg"): "Images",
        ("application/octet-stream", "a.zip"): "Archives",
    }
    for (mime, fn), expected in cases.items():
        assert category_for(mime, fn) == expected, (mime, fn)


def test_category_for_real_mime_wins_and_unknown():
    from mboxviewer.filetypes import category_for
    assert category_for("application/pdf", "x.bin") == "Documents"     # real mime wins
    assert category_for("application/octet-stream", "x.dat") == "Other"  # unknown ext
    assert category_for("application/octet-stream", "") == "Other"
```

- [ ] **Step 2: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_filetypes.py -k "extension_fallback or real_mime" -v`

- [ ] **Step 3: `filetypes.py`.** Add `import os` at the top. Add the extension map + `category_for` (after `category_for_mime`):
```python
_EXT_CATEGORY = {}
for _cat, _exts in {
    "Documents": ".pdf .doc .docx .rtf .odt .txt .md .pages .docm",
    "Spreadsheets": ".xls .xlsx .ods .numbers .xlsm",
    "Presentations": ".ppt .pptx .pps .ppsx .odp .key .pptm",
    "Images": ".jpg .jpeg .png .gif .bmp .webp .tif .tiff .heic .heif",
    "Archives": ".zip .rar .7z .gz .tar .bz2 .tgz .jar",
    "Calendar": ".ics .vcs",
    "Contacts": ".vcf",
    "Media": (".mp3 .m4a .wav .aac .ogg .oga .flac .opus .wma "
              ".mp4 .m4v .mov .avi .wmv .mkv .webm .mpg .mpeg .3gp"),
}.items():
    for _e in _exts.split():
        _EXT_CATEGORY[_e] = _cat


def category_for(mime, filename):
    """Category from MIME, falling back to the filename extension when the MIME
    is generic/unknown (maps to 'Other')."""
    cat = category_for_mime(mime)
    if cat != "Other":
        return cat
    ext = os.path.splitext(filename or "")[1].lower()
    return _EXT_CATEGORY.get(ext, "Other")
```

- [ ] **Step 4: Run, verify PASS** (full filetypes suite).
Run: `.venv/bin/pytest tests/test_filetypes.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/filetypes.py tests/test_filetypes.py
git commit -m "feat: category_for with filename-extension fallback"
```

---

## Task 2: store + indexer — category column

**Files:** Modify `src/mboxviewer/store.py`, `src/mboxviewer/indexer.py`; Modify `tests/test_store.py`.

- [ ] **Step 1: Failing test — REPLACE `test_attachment_mime_counts_and_files` in `tests/test_store.py`** with:
```python
def test_category_counts_and_listing(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["category"]: r["c"] for r in s.attachment_category_counts()}
    assert counts.get("Documents") == 2          # invoice.pdf + report.docx
    files = s.list_files_by_category("Documents", 10, 0)
    assert sorted(f["filename"] for f in files) == ["invoice.pdf", "report.docx"]
    assert s.list_files_by_category("Documents", 10, 0, query="invoice")[0]["filename"] == "invoice.pdf"
    assert s.list_files_by_category(None, 10, 0) == []          # no category, no query
    assert s.list_files_by_category("Nope", 10, 0) == []        # unknown category


def test_octet_stream_pdf_categorized_by_extension(tmp_path):
    import io
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(b"%PDF-1.4 junk", maintype="application", subtype="octet-stream",
                     filename="scan.pdf")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "o.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["category"]: r["c"] for r in s.attachment_category_counts()}
    assert counts.get("Documents") == 1          # octet-stream + .pdf -> Documents


def test_category_column_migration(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    # Simulate a pre-existing attachments table WITHOUT the category column.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE attachments (id INTEGER PRIMARY KEY, message_id INTEGER, idx INTEGER,"
        " filename TEXT, mime TEXT, size INTEGER);"
        "INSERT INTO attachments(message_id, idx, filename, mime, size) VALUES(1,0,'a.pdf','x',1);")
    conn.commit(); conn.close()
    s = Store(db)
    s.create_schema()    # must ALTER-add category without data loss
    s.create_schema()    # idempotent
    assert s.conn.execute("SELECT category FROM attachments").fetchone()[0] is None
```

- [ ] **Step 2: Run, verify FAIL** (`AttributeError`/no column).
Run: `.venv/bin/pytest tests/test_store.py -k "category or octet or migration" -v`

- [ ] **Step 3: `store.py` schema + migration.** In `SCHEMA`, change the attachments table to include the column:
```sql
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  idx INTEGER NOT NULL,
  filename TEXT,
  mime TEXT,
  size INTEGER,
  category TEXT
);
```
Replace `create_schema` with:
```python
    def create_schema(self):
        self.conn.executescript(SCHEMA)
        try:
            self.conn.execute("ALTER TABLE attachments ADD COLUMN category TEXT")
        except sqlite3.OperationalError:
            pass  # column already present (fresh CREATE or prior migration)
        self.conn.commit()
```

- [ ] **Step 4: `store.py` methods.** Replace `add_attachment` with:
```python
    def add_attachment(self, message_id, idx, filename, mime, size, category):
        self.conn.execute(
            "INSERT INTO attachments(message_id,idx,filename,mime,size,category)"
            " VALUES(?,?,?,?,?,?)",
            (message_id, idx, filename, mime, size, category))
```
Replace `attachment_mime_counts` with:
```python
    def attachment_category_counts(self):
        return self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM attachments GROUP BY category").fetchall()
```
Replace `list_files_by_mimes` with `list_files_by_category`:
```python
    def list_files_by_category(self, category, limit, offset, query=None):
        where = []
        params = []
        if category:
            where.append("a.category = ?")
            params.append(category)
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

- [ ] **Step 5: `indexer.py`.** Add `from . import filetypes` (next to the other imports). In the attachment loop, compute and pass the category:
```python
                for idx, filename, mime, payload in iter_attachments(msg):
                    category = filetypes.category_for(mime, filename)
                    store.add_attachment(mid, idx, filename, mime, len(payload), category)
                    att_texts.append(extract_text(filename, mime, payload))
```

- [ ] **Step 6: Run, verify PASS** (full store suite).
Run: `.venv/bin/pytest tests/test_store.py -v`

- [ ] **Step 7: Commit.**
```bash
git add src/mboxviewer/store.py src/mboxviewer/indexer.py tests/test_store.py
git commit -m "feat: store attachment category (extension-aware) with counts + listing"
```

---

## Task 3: api — filetypes/files by category

**Files:** Modify `src/mboxviewer/api.py`; Modify `tests/test_api.py`.

- [ ] **Step 1: Update tests in `tests/test_api.py`.** The existing `test_filetypes_endpoint`,
  `test_files_by_category`, `test_files_unknown_category_empty`,
  `test_files_unknown_category_with_query_still_empty`, `test_files_search_within_category`,
  `test_files_search_no_category`, `test_files_no_category_no_query_empty`, and `test_file_text_endpoint`
  must still pass with the new backend (the sample's pdf+docx are Documents). They should already be
  compatible (same shapes); run them in Step 4. Add one new test:
```python
def test_filetypes_uses_stored_category(client):
    cats = {c["category"]: c["count"] for c in client.get("/api/filetypes").json()}
    assert cats.get("Documents") == 2  # from the stored category column, not mime aggregation
```

- [ ] **Step 2: Replace `/api/filetypes` and `/api/files` in `api.py`.**
```python
    @app.get("/api/filetypes")
    def filetypes_route():
        counts = {r["category"]: r["c"] for r in store.attachment_category_counts()}
        return [{"category": cat, "count": counts[cat]}
                for cat in filetypes.CATEGORY_ORDER if counts.get(cat)]

    @app.get("/api/files")
    def files(category: Optional[str] = None, q: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        query = (q or "").strip()
        if not category and not query:
            return {"files": [], "page": page}
        offset = (page - 1) * page_size
        rows = store.list_files_by_category(category or None, page_size, offset, query=query or None)
        return {"files": [{"message_id": r["message_id"], "idx": r["idx"],
                           "filename": r["filename"], "size": r["size"], "mime": r["mime"],
                           "subject": r["subject"], "date": r["date"]} for r in rows],
                "page": page}
```
(The `Counter` import may become unused — leave other usages intact; if `Counter` is now unused, remove its import line.)

- [ ] **Step 3: Run, verify PASS** (the full api suite — all prior files/filetypes tests).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 4: Run full suite.**
Run: `.venv/bin/pytest -q`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: /api/filetypes and /api/files use the stored category column"
```

---

## Task 4: extract — legacy .ppt/.pps via catppt

**Files:** Modify `src/mboxviewer/extract.py`, `Dockerfile`; Modify `tests/test_extract.py`.

- [ ] **Step 1: Failing test — add to `tests/test_extract.py`:**
```python
def test_ppt_dispatch_without_catppt_returns_empty():
    # catppt is absent in the dev venv, so the ppt path returns "" (no crash).
    from mboxviewer.extract import extract_text
    assert extract_text("d.ppt", "application/vnd.ms-powerpoint", b"\xd0\xcf\x11\xe0junk") == ""


def test_ppt_text_uses_catppt(monkeypatch):
    # Simulate catppt being present and returning text.
    import subprocess
    from mboxviewer import extract
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/catppt" if name == "catppt" else None)
    class _R:
        returncode = 0
        stdout = b"SLIDE ONE TITLE"
    monkeypatch.setattr(extract.subprocess, "run", lambda *a, **k: _R())
    assert "SLIDE ONE TITLE" in extract.extract_text("d.ppt", "application/vnd.ms-powerpoint", b"x")
```

- [ ] **Step 2: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_extract.py -k "ppt" -v`

- [ ] **Step 3: `extract.py`.** Add a generic helper and the dispatch. Add near `_antiword_text`:
```python
def _run_text_tool(toolname: str, suffix: str, data: bytes) -> str:
    exe = shutil.which(toolname)
    if not exe:
        return ""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            path = f.name
            f.write(data)
        proc = subprocess.run([exe, path], capture_output=True, timeout=30)
        return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else ""
    except Exception:
        return ""
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _ppt_text(data: bytes) -> str:
    return _run_text_tool("catppt", ".ppt", data)
```
(Optionally refactor `_antiword_text` to `return _run_text_tool("antiword", ".doc", data)` — do so to avoid duplication.)
Add the dispatch inside `extract_text`'s `try` (with the office exact-matches):
```python
        if mime in ("application/vnd.ms-powerpoint", "application/mspowerpoint"):
            return _ppt_text(data)
```

- [ ] **Step 4: `Dockerfile`** — add `catdoc` (provides `catppt`) to the apt install line:
```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword catdoc \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 5: Run, verify PASS** (full extract suite).
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/extract.py Dockerfile tests/test_extract.py
git commit -m "feat: legacy .ppt/.pps text extraction via catppt"
```

---

## Task 5: Frontend — spreadsheet table, sticky search, WMA message

**Files:** Modify `src/mboxviewer/static/app.js`, `static/style.css`. No unit tests; verified in Task 6.

- [ ] **Step 1: `style.css` — sticky search.** Replace the `#searchbar` rule:
```css
#searchbar { padding: 10px; border-bottom: 1px solid #ddd; }
```
with:
```css
#searchbar { padding: 10px; border-bottom: 1px solid #ddd; position: sticky; top: 0;
  background: #fff; z-index: 2; }
```

- [ ] **Step 2: `app.js` — generalize the table renderer + add parsers/sets.** Replace `renderCsvTable`
  (the whole function) with:
```javascript
function renderTableRows(rows) {
  const CAP = 500;
  const shown = rows.slice(0, CAP + 1); // +1 header
  const head = shown[0] || [];
  const body = shown.slice(1);
  const th = head.map(c => `<th>${escapeHtml(c)}</th>`).join("");
  const trs = body.map(r => "<tr>" + r.map(c => `<td>${escapeHtml(c)}</td>`).join("") + "</tr>").join("");
  let html = `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  if (rows.length - 1 > CAP) html += `<p class="csv-note">Showing first ${CAP} of ${rows.length - 1} rows.</p>`;
  return html;
}

function renderCsvTable(text) { return renderTableRows(parseCsv(text)); }

function parseTsv(text) {
  return text.split("\n").filter(l => l !== "").map(l => l.split("\t"));
}

const _SPREADSHEET_MIMES = new Set([
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.oasis.opendocument.spreadsheet",
  "application/x-msexcel", "application/msexcel", "application/excel",
]);
function isSpreadsheet(m, name) {
  return _SPREADSHEET_MIMES.has(m) || /\.(xls|xlsx|ods|xlsm)$/.test(name);
}

const _UNPLAYABLE_MIMES = new Set([
  "audio/x-ms-wma", "video/x-ms-wmv", "video/x-ms-asf", "audio/x-ms-wax",
]);
function isUnplayable(m, name) {
  return _UNPLAYABLE_MIMES.has(m) || /\.(wma|wmv|asf)$/.test(name);
}
```

- [ ] **Step 3: `app.js` — rewrite the audio/video dispatch in `openFile`.** Replace these two lines:
```javascript
  if (m.startsWith("audio/")) { showOnlyPane(readerAudio); readerAudio.src = inlineUrl; return; }
  if (m.startsWith("video/")) { showOnlyPane(readerVideo); readerVideo.src = inlineUrl; return; }
```
with:
```javascript
  if (m.startsWith("audio/") || m.startsWith("video/")) {
    if (isUnplayable(m, name)) {
      showOnlyPane(readerText);
      readerText.textContent = "This format (Windows Media) can't be played in the browser. Use the Download link above.";
      return;
    }
    const pane = m.startsWith("audio/") ? readerAudio : readerVideo;
    showOnlyPane(pane);
    pane.onerror = () => {
      showOnlyPane(readerText);
      readerText.textContent = "This file couldn't be played in the browser. Use the Download link above.";
    };
    pane.src = inlineUrl;
    return;
  }
```

- [ ] **Step 4: `app.js` — add the spreadsheet branch.** Immediately AFTER the `text/csv` branch
  (the one ending `return; }` around the CSV handler) and BEFORE the `application/ms-tnef` branch, insert:
```javascript
  if (isSpreadsheet(m, name)) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim()) ? renderTableRows(parseTsv(d.text)) : "No content.";
    } catch (err) {
      readerTable.textContent = "Failed to load file: " + err.message;
    }
    return;
  }
```

- [ ] **Step 5: Verify.**
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `node --check src/mboxviewer/static/app.js && echo ok` (if node present)
Run: `grep -n "isSpreadsheet\|isUnplayable\|parseTsv\|renderTableRows\|onerror" src/mboxviewer/static/app.js` — report.
Run: `.venv/bin/pytest -q` (unchanged).

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: spreadsheet table view, sticky search, and WMA/WMV notice"
```

---

## Task 6: End-to-end verification + redeploy + re-index

**Files:** none.

- [ ] **Step 1: Local sample** — an octet-stream `.pdf`, an `.xlsx`, and a (fake) `.wmv`. Run the viewer:
```bash
rm -rf /tmp/cx.db* /tmp/cxarch /tmp/cx.mbox 2>/dev/null
.venv/bin/python - <<'PY'
import io
from email.message import EmailMessage
from email.generator import BytesGenerator
import openpyxl
def xlsx(rows):
    b=io.BytesIO(); wb=openpyxl.Workbook(); ws=wb.active
    for r in rows: ws.append(r)
    wb.save(b); return b.getvalue()
def email(sub, atts):
    m=EmailMessage(); m["Subject"]=sub; m["From"]="a@x.com"; m["To"]="b@x.com"
    m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
    m.set_content("body"); m.add_alternative("<p>x</p>", subtype="html")
    for fn,mt,st,d in atts: m.add_attachment(d, maintype=mt, subtype=st, filename=fn)
    return m
XLSX="vnd.openxmlformats-officedocument.spreadsheetml.sheet"
msgs=[email("Scan",[("scan.pdf","application","octet-stream",b"%PDF-1.4 fake")]),
      email("Numbers",[("data.xlsx","application",XLSX,xlsx([["Region","Sales"],["EMEA",4200]]))]),
      email("Clip",[("movie.wmv","application","octet-stream",b"fakewmv")])]
with open("/tmp/cx.mbox","wb") as f:
    for m in msgs:
        d=io.BytesIO(); BytesGenerator(d).flatten(m); d=d.getvalue()
        f.write(b"From - x\n"+d+(b"" if d.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/cx.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/cx.mbox INDEX_PATH=/tmp/cx.db ARCHIVE_DIR=/tmp/cxarch \
  HOST=127.0.0.1 PORT=8141 .venv/bin/python -m mboxviewer.main
```
Terminal 2:
```bash
curl -s http://127.0.0.1:8141/api/filetypes      # Documents:1, Spreadsheets:1, Media:1 (no Other)
curl -s "http://127.0.0.1:8141/api/files?category=Documents" | .venv/bin/python -m json.tool  # scan.pdf
```

- [ ] **Step 2: Browser** at http://127.0.0.1:8141 (Files mode):
  - **Documents** lists `scan.pdf` (the octet-stream pdf — fixed categorization). **Media** lists
    `movie.wmv`; opening it shows the "can't be played" message + Download. **Spreadsheets** → `data.xlsx`
    → a **table** (Region|Sales / EMEA|4200).
  - Scroll a long category's file list → the **search box stays pinned** at the top.
  Stop the server; `rm -rf /tmp/cx.mbox /tmp/cx.db* /tmp/cxarch`.

- [ ] **Step 3: Redeploy + re-index** (Dockerfile + schema changed). The category column needs a fresh
  populate, so clear the index and re-index:
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
docker run --rm -v mbox_mbox-index:/index alpine sh -c "rm -f /index/index.db*"
./run.sh    # rebuild installs catdoc; serves immediately, re-indexes in the background
```
Verify at http://localhost:9000 once indexed: **Other shrinks dramatically** (the ~800 PDFs, 87 docs,
50 jpgs, etc. move to Documents/Images/Media/…; Other ≈ winmail.dat + signatures + genuine unknowns);
a real octet-stream `.pdf` appears under Documents; a real `.ppt` extracts text (catppt); a real `.xls`
shows a table; a real `.wmv` shows the notice.

---

## Self-Review Notes
- **Spec coverage:** extension fallback + stored category (Tasks 1-3) ✓; legacy ppt (Task 4) ✓;
  spreadsheet table + sticky search + WMA notice (Task 5) ✓; e2e + re-index (Task 6) ✓.
- **Consistency:** `category_for(mime, filename)` used by the indexer; counts/listing key on the
  `category` column; `/api/files` still accepts `category`+`q` with the same response shape (no frontend
  category change); `renderTableRows` shared by CSV + spreadsheet; `isSpreadsheet`/`isUnplayable` sets
  match the extension map; `_run_text_tool` shared by antiword + catppt.
- **Migration:** `create_schema` ALTER-adds `category` to old DBs; fresh index (cleared before re-index)
  gets it from CREATE. Re-index repopulates.
- **No placeholders:** all steps complete; ppt-with-catppt tested via monkeypatch (tool absent locally).
