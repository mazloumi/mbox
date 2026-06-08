# Archive Listing + Index Inner Filenames — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a zip/archive lists its inner files (Name · Size) as a table in the reader, and the inner filenames become full-text searchable.

**Architecture:** `extract.py` returns a tab-separated `Name\tSize` listing for archives (stdlib `zipfile`/`tarfile`); this both feeds the FTS index (searchable names) and is rendered by the existing CSV/spreadsheet table path in the frontend. `SCHEMA_VERSION` bumps so the schema-version guard auto-re-indexes.

**Tech Stack:** Python 3.9 (stdlib zipfile/tarfile — no new deps), vanilla JS. Tests: pytest + testclient. **Requires re-index** (handled by the guard on deploy).

**Spec:** `docs/superpowers/specs/2026-06-08-archive-listing-design.md`

---

## File Structure
```
src/mboxviewer/extract.py        # MODIFY — archive listing + dispatch
src/mboxviewer/store.py          # MODIFY — SCHEMA_VERSION 2 -> 3
src/mboxviewer/static/app.js     # MODIFY — archive branch in openFile
tests/test_extract.py, test_indexer.py  # MODIFY
```

---

## Task 1: extract — archive listing

**Files:** Modify `src/mboxviewer/extract.py`; Modify `tests/test_extract.py`.

- [ ] **Step 1: Failing tests — add to `tests/test_extract.py`:**
```python
def test_archive_zip_listing():
    import io, zipfile
    from mboxviewer.extract import extract_text, _is_archive
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("docs/report.pdf", b"x" * 1500)
        z.writestr("secret_plans.txt", b"hi")
        z.writestr("emptydir/", b"")            # directory entry -> skipped
    out = extract_text("bundle.zip", "application/zip", zb.getvalue())
    assert "Name\tSize" in out
    assert "docs/report.pdf" in out and "secret_plans.txt" in out
    assert "emptydir/" not in out
    assert _is_archive("application/octet-stream", "x.zip")    # extension path


def test_archive_targz_listing():
    import io, tarfile
    from mboxviewer.extract import extract_text
    tb = io.BytesIO()
    with tarfile.open(fileobj=tb, mode="w:gz") as t:
        data = b"r,c\n1,2"
        ti = tarfile.TarInfo("a/data.csv"); ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    out = extract_text("arch.tar.gz", "application/gzip", tb.getvalue())
    assert "a/data.csv" in out


def test_archive_non_archive_and_empty():
    import io, zipfile
    from mboxviewer.extract import extract_text
    assert extract_text("x.zip", "application/zip", b"not a zip") == ""
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("onlydir/", b"")
    assert extract_text("d.zip", "application/zip", zb.getvalue()) == ""  # no files
```

- [ ] **Step 2: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_extract.py -k archive -v`

- [ ] **Step 3: `extract.py`.** Add `import zipfile`, `import tarfile` near the top imports. Add constants near the other MIME sets:
```python
_ARCHIVE_MIMES = {
    "application/zip", "application/x-zip-compressed", "application/java-archive",
    "application/gzip", "application/x-gzip", "application/x-tar", "application/x-gtar",
    "application/x-bzip-compressed-tar",
}
_ARCHIVE_EXTS = (".zip", ".jar", ".war", ".ear", ".tar", ".tgz", ".tbz2", ".tar.gz", ".tar.bz2")
```
Add helpers (e.g. after the TNEF helpers):
```python
def _is_archive(mime, filename):
    if (mime or "").lower() in _ARCHIVE_MIMES:
        return True
    name = (filename or "").lower()
    return name.endswith(_ARCHIVE_EXTS)


def iter_archive_entries(data, cap=5000):
    """[(name, size)] of the files inside a zip or tar archive (dirs skipped)."""
    buf = io.BytesIO(data)
    if zipfile.is_zipfile(buf):
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            out = []
            for info in z.infolist():
                if info.is_dir():
                    continue
                out.append((info.filename, info.file_size))
                if len(out) >= cap:
                    break
            return out
    buf.seek(0)
    try:
        with tarfile.open(fileobj=buf) as t:
            out = []
            for m in t:
                if m.isfile():
                    out.append((m.name, m.size))
                    if len(out) >= cap:
                        break
            return out
    except Exception:
        return []


def _human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d %s" % (int(n), unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024


def _archive_text(data):
    rows = iter_archive_entries(data)
    if not rows:
        return ""
    lines = ["Name\tSize"]
    for name, size in rows:
        lines.append("%s\t%s" % (name, _human_size(size)))
    return "\n".join(lines)
```
Add the dispatch inside `extract_text`'s `try`, before the calendar/text branches (after the office/tnef exact-matches):
```python
        if _is_archive(mime, filename):
            return _archive_text(data)
```

- [ ] **Step 4: Run, verify PASS** (full extract suite).
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/extract.py tests/test_extract.py
git commit -m "feat: list zip/tar archive contents as searchable Name/Size text"
```

---

## Task 2: store — bump SCHEMA_VERSION + verify inner-name indexing

**Files:** Modify `src/mboxviewer/store.py`; Modify `tests/test_indexer.py`.

- [ ] **Step 1: Failing test — add to `tests/test_indexer.py`:**
```python
def test_zip_inner_filenames_are_indexed(tmp_path):
    import io, zipfile
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("secret_plans.txt", b"hi")
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(zb.getvalue(), maintype="application", subtype="zip", filename="bundle.zip")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "z.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path); store.create_schema(); build_index(settings, store)
    assert len(store.search("secret_plans", None, 10, 0)) == 1   # inner filename searchable
```

- [ ] **Step 2: Run, verify it PASSES already** (Task 1 made extract emit the names, which the indexer
  already folds into FTS). If it does, good — this test locks the behavior. If the term isn't found,
  STOP (the FTS tokenizer may split on `_`; the test uses `secret_plans` which tokenizes to
  `secret`+`plans` — search the same way). Run:
Run: `.venv/bin/pytest tests/test_indexer.py::test_zip_inner_filenames_are_indexed -v`

- [ ] **Step 3: Bump `SCHEMA_VERSION` in `src/mboxviewer/store.py`** from `2` to `3` (so the guard
  re-indexes existing indexes to pick up archive listings). Update the comment's example value if it
  cites a number.

- [ ] **Step 4: Add a guard test — add to `tests/test_indexer.py`:**
```python
def test_schema_version_bump_marks_v2_index_stale(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    store.set_meta("schema_version", "2"); store.commit()   # built by the previous format
    assert index_is_current(settings, store) is False
```

- [ ] **Step 5: Run, verify PASS** (full suite).
Run: `.venv/bin/pytest -q`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/store.py tests/test_indexer.py
git commit -m "feat: bump SCHEMA_VERSION to 3 so archive listings get re-indexed"
```

---

## Task 3: Frontend — archive table in the reader

**Files:** Modify `src/mboxviewer/static/app.js`. No unit tests; verified in Task 4.

- [ ] **Step 1: `app.js` — add the archive set + predicate.** After the `_UNPLAYABLE_MIMES`/`isUnplayable`
  block, add:
```javascript
const _ARCHIVE_MIMES = new Set([
  "application/zip", "application/x-zip-compressed", "application/java-archive",
  "application/gzip", "application/x-gzip", "application/x-tar", "application/x-gtar",
  "application/x-bzip-compressed-tar",
]);
function isArchive(m, name) {
  return _ARCHIVE_MIMES.has(m) || /\.(zip|jar|war|ear|tar|tgz|tbz2|tar\.gz|tar\.bz2|gz|bz2)$/.test(name);
}
```

- [ ] **Step 2: `app.js` — add the archive branch in `openFile`.** Immediately AFTER the spreadsheet
  branch (the `if (isSpreadsheet(m, name)) { … return; }` block) and BEFORE the
  `if (m === "application/ms-tnef" …)` branch, insert:
```javascript
  if (isArchive(m, name)) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim()) ? renderTableRows(parseTsv(d.text)) : "No files listed.";
    } catch (err) {
      readerTable.textContent = "Failed to load archive: " + err.message;
    }
    return;
  }
```

- [ ] **Step 3: Verify.**
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `node --check src/mboxviewer/static/app.js && echo ok` (if node present)
Run: `grep -n "isArchive\|_ARCHIVE_MIMES" src/mboxviewer/static/app.js` — report.
Run: `.venv/bin/pytest -q` (unchanged).

- [ ] **Step 4: Commit.**
```bash
git add src/mboxviewer/static/app.js
git commit -m "feat: render zip/tar archive contents as a table in the reader"
```

---

## Task 4: End-to-end verification + redeploy

**Files:** none.

- [ ] **Step 1: Local sample** — an email with a `.zip` containing a couple of files. Run the viewer:
```bash
rm -rf /tmp/az.db* /tmp/azarch /tmp/az.mbox 2>/dev/null
.venv/bin/python - <<'PY'
import io, zipfile
from email.message import EmailMessage
from email.generator import BytesGenerator
zb=io.BytesIO()
with zipfile.ZipFile(zb,"w") as z:
    z.writestr("docs/Q3_report.pdf", b"x"*120000)
    z.writestr("budget_2024.xlsx", b"y"*8000)
    z.writestr("readme.txt", b"hello")
m=EmailMessage(); m["Subject"]="Bundle"; m["From"]="a@x.com"; m["To"]="b@x.com"
m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
m.set_content("body"); m.add_alternative("<p>x</p>", subtype="html")
m.add_attachment(zb.getvalue(), maintype="application", subtype="zip", filename="bundle.zip")
with open("/tmp/az.mbox","wb") as f:
    d=io.BytesIO(); BytesGenerator(d).flatten(m); d=d.getvalue()
    f.write(b"From - x\n"+d+(b"" if d.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/az.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/az.mbox INDEX_PATH=/tmp/az.db ARCHIVE_DIR=/tmp/azarch \
  HOST=127.0.0.1 PORT=8142 .venv/bin/python -m mboxviewer.main
```
Terminal 2:
```bash
# the zip is in Archives; list its contents via /text:
curl -s "http://127.0.0.1:8142/api/files?category=Archives" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])" | { read M I; curl -s "http://127.0.0.1:8142/api/files/$M/$I/text"; }
# inner filename is searchable:
curl -s "http://127.0.0.1:8142/api/search?q=Q3_report" | .venv/bin/python -c "import sys,json;print('hits:', len(json.load(sys.stdin)['messages']))"
```

- [ ] **Step 2: Browser** at http://127.0.0.1:8142 (Files mode):
  - **Archives** → `bundle.zip` → the reader shows a **table** with `docs/Q3_report.pdf`,
    `budget_2024.xlsx`, `readme.txt` and their sizes.
  - The search box (Folders mode) for `Q3_report` finds the "Bundle" email.
  Stop the server; `rm -rf /tmp/az.mbox /tmp/az.db* /tmp/azarch`.

- [ ] **Step 3: Redeploy.** No new deps; `SCHEMA_VERSION` bumped → the guard re-indexes automatically
  (do NOT clear the index — verify the guard triggers it):
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Confirm at http://localhost:9000: it starts re-indexing on its own (the guard fired on the v2→v3
bump). Once indexed: open a real `.zip`/`.jar` under Archives → table of contents; search a known
inner filename → the email is found.

---

## Self-Review Notes
- **Spec coverage:** zip + tar listing as text (Task 1) ✓; inner names indexed + SCHEMA_VERSION bump
  (Task 2) ✓; table render in reader (Task 3) ✓; e2e + guard-driven re-index (Task 4) ✓.
- **Consistency:** `_archive_text` emits the same tab-separated shape the spreadsheet path already
  renders via `renderTableRows(parseTsv(...))`; `_ARCHIVE_MIMES`/`isArchive` mirror the backend set;
  `_is_archive` keys on mime OR extension (octet-stream zips). Security: zip metadata-only (no
  decompression), tar entry-capped, names escaped in the table, no per-entry extraction.
- **Re-index:** SCHEMA_VERSION 2→3 makes the guard re-index existing indexes — verified live in Task 4.
- **No placeholders:** all code is prototype-verified.
