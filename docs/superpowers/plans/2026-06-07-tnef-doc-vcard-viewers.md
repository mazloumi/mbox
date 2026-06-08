# TNEF / Legacy .doc / vCard Viewers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract/view three locked-up attachment classes: vCard contacts (new Contacts category), legacy `.doc` (best-effort), and TNEF `winmail.dat` (unwrap + list contained files with Download links).

**Architecture:** All extraction is on-demand in `extract.py`. vCard/`.doc` render in the existing text pane (vCard via a new Contacts category). TNEF adds two endpoints to list/serve a `winmail.dat`'s inner files (the wrapper stays one top-level attachment — `reader.iter_attachments` and contract #1 are untouched) and a frontend branch that shows the unwrapped text + a contained-files list.

**Tech Stack:** Python 3.9, FastAPI, SQLite, vanilla JS. New deps: `olefile`, `tnefparse` (pure-Python). `antiword` in the Docker image only. Tests: pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-06-07-tnef-doc-vcard-viewers-design.md`

---

## File Structure

```
requirements.txt                 # MODIFY — olefile, tnefparse
Dockerfile                       # MODIFY — apt-get antiword
src/mboxviewer/filetypes.py      # MODIFY — Contacts category + vCard mimes
src/mboxviewer/extract.py        # MODIFY — _vcard_text, _doc_text/_salvage_text, _tnef_text, iter_tnef_attachments
src/mboxviewer/api.py            # MODIFY — TNEF /inner and /inner/{k}
src/mboxviewer/static/app.js     # MODIFY — openFile TNEF branch
tests/test_filetypes.py          # MODIFY
tests/test_extract.py            # MODIFY
tests/test_api.py                # MODIFY
```

---

## Task 1: vCard — Contacts category + extraction

**Files:** Modify `src/mboxviewer/filetypes.py`, `src/mboxviewer/extract.py`; Modify `tests/test_filetypes.py`, `tests/test_extract.py`.

- [ ] **Step 1: Failing filetypes test — add to `tests/test_filetypes.py`:**
```python
def test_contacts_mimes():
    from mboxviewer.filetypes import CATEGORY_ORDER
    for m in ["text/x-vcard", "text/vcard", "application/vcard"]:
        assert category_for_mime(m) == "Contacts", m
    assert "Contacts" in CATEGORY_ORDER
```

- [ ] **Step 2: Failing extract test — add to `tests/test_extract.py`:**
```python
def test_extract_vcard():
    from mboxviewer.extract import extract_text
    vcf = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice Smith\r\nORG:Acme\r\n"
           "TITLE:Engineer\r\nEMAIL:alice@example.com\r\n"
           "EMAIL;TYPE=work:a.smith@acme.com\r\nTEL:+1-555-1234\r\nEND:VCARD\r\n")
    out = extract_text("c.vcf", "text/x-vcard", vcf.encode())
    assert "Name: Alice Smith" in out
    assert "alice@example.com" in out and "a.smith@acme.com" in out
    assert "+1-555-1234" in out and "Acme" in out


def test_extract_vcard_no_card_empty():
    from mboxviewer.extract import extract_text
    assert extract_text("x.vcf", "text/x-vcard", b"not a vcard") == ""
```

- [ ] **Step 3: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_filetypes.py::test_contacts_mimes tests/test_extract.py -k vcard -v`

- [ ] **Step 4: `filetypes.py`.** Add `"Contacts"` to `CATEGORY_ORDER` immediately after `"Calendar"`:
```python
CATEGORY_ORDER = [
    "Documents", "Spreadsheets", "Presentations", "Images",
    "Archives", "Calendar", "Contacts", "Media", "Other",
]
```
Add a module-level set near `_CALENDAR`:
```python
# Keep in sync with extract._VCARD_MIMES (separate module, no shared import).
_CONTACTS = {"text/x-vcard", "text/vcard", "application/vcard", "text/directory"}
```
In `category_for_mime`, add this check immediately after the `_CALENDAR` check:
```python
    if m in _CONTACTS:
        return "Contacts"
```

- [ ] **Step 5: `extract.py`.** Add constant near `_CALENDAR_MIMES`:
```python
# Keep in sync with filetypes._CONTACTS (separate module, no shared import).
_VCARD_MIMES = {"text/x-vcard", "text/vcard", "application/vcard", "text/directory"}
```
Add this helper (after `_ics_text`). It reuses `_ics_unescape` and the same unfolding rule:
```python
def _vcard_text(data: bytes) -> str:
    raw = data.decode("utf-8", "replace")
    lines = []
    for line in raw.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    cards = []
    cur = None
    for line in lines:
        u = line.strip().upper()
        if u == "BEGIN:VCARD":
            cur = {"emails": [], "tels": []}
        elif u == "END:VCARD":
            if cur is not None:
                cards.append(cur)
                cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";", 1)[0].upper()
            value = _ics_unescape(value)
            if key == "FN":
                cur["name"] = value
            elif key == "N" and "name" not in cur:
                cur["name"] = value.replace(";", " ").strip()
            elif key == "EMAIL":
                cur["emails"].append(value)
            elif key == "TEL":
                cur["tels"].append(value)
            elif key == "ORG":
                cur["org"] = value.replace(";", " ").strip()
            elif key == "TITLE":
                cur["title"] = value
            elif key == "ADR":
                cur["adr"] = value.replace(";", " ").strip()
            elif key == "URL":
                cur["url"] = value
    out = []
    for c in cards:
        if c.get("name"):
            out.append("Name: " + c["name"])
        if c.get("title"):
            out.append("Title: " + c["title"])
        if c.get("org"):
            out.append("Organization: " + c["org"])
        if c.get("emails"):
            out.append("Email: " + ", ".join(c["emails"]))
        if c.get("tels"):
            out.append("Phone: " + ", ".join(c["tels"]))
        if c.get("adr"):
            out.append("Address: " + c["adr"])
        if c.get("url"):
            out.append("URL: " + c["url"])
        out.append("")
    return "\n".join(out).strip()
```
Add the dispatch branch inside `extract_text`'s `try`, with the other line-based formats (before `text/html`/`text/`):
```python
        if mime in _VCARD_MIMES:
            return _vcard_text(data)
```

- [ ] **Step 6: Run, verify PASS** (extract + filetypes suites).
Run: `.venv/bin/pytest tests/test_extract.py tests/test_filetypes.py -v`

- [ ] **Step 7: Commit.**
```bash
git add src/mboxviewer/filetypes.py src/mboxviewer/extract.py tests/test_filetypes.py tests/test_extract.py
git commit -m "feat: vCard contact extraction and Contacts category"
```

---

## Task 2: Legacy .doc extraction (antiword + OLE salvage)

**Files:** Modify `src/mboxviewer/extract.py`, `requirements.txt`, `Dockerfile`; Modify `tests/test_extract.py`.

- [ ] **Step 1: Add deps.** Append to `requirements.txt`:
```
olefile==0.47
tnefparse==1.4.0
```
(tnefparse is added now so Task 3 needs no second dep change.)
Install locally: `.venv/bin/pip install olefile==0.47 tnefparse==1.4.0`

- [ ] **Step 2: `Dockerfile`** — add antiword before the pip install. Replace:
```dockerfile
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```
with:
```dockerfile
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

- [ ] **Step 3: Failing tests — add to `tests/test_extract.py`:**
```python
def test_doc_salvage_text_utf16():
    from mboxviewer.extract import _salvage_text
    raw = b"\x07\x00" + "Quarterly Report Growth".encode("utf-16-le") + b"\x00\x13"
    assert "Quarterly Report Growth" in _salvage_text(raw)


def test_doc_salvage_text_cp1252():
    from mboxviewer.extract import _salvage_text
    raw = b"\x00\x01" + "Hello cp1252 World".encode("cp1252") + b"\xff"
    assert "Hello cp1252 World" in _salvage_text(raw)


def test_extract_doc_non_ole_returns_empty():
    from mboxviewer.extract import extract_text
    assert extract_text("a.doc", "application/msword", b"not an ole file") == ""
```

- [ ] **Step 4: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_extract.py -k "salvage or doc_non_ole" -v`

- [ ] **Step 5: `extract.py`.** Add helpers (after the office helpers). Add `import shutil`, `import subprocess`, `import tempfile`, `import os` near the top imports (some may already be present — add only the missing ones):
```python
def _salvage_text(raw: bytes) -> str:
    """Best-effort text recovery from a binary stream: try UTF-16LE and CP1252,
    keep printable runs, return whichever has more alphabetic content."""
    def clean(s):
        s = "".join(ch if (ch.isprintable() or ch in "\n\t ") else " " for ch in s)
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\n{2,}", "\n", s)
        return s.strip()
    a = clean(raw.decode("utf-16-le", "ignore"))
    b = clean(raw.decode("cp1252", "ignore"))
    score = lambda s: sum(c.isalpha() for c in s)
    return a if score(a) >= score(b) else b


def _antiword_text(data: bytes) -> str:
    exe = shutil.which("antiword")
    if not exe:
        return ""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
            f.write(data)
            path = f.name
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


def _doc_text(data: bytes) -> str:
    text = _antiword_text(data)        # accurate when antiword is installed (Docker image)
    if text.strip():
        return text
    import olefile                     # pure-Python fallback (dev venv, or antiword miss)
    if not olefile.isOleFile(io.BytesIO(data)):
        return ""
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if not ole.exists("WordDocument"):
            return ""
        raw = ole.openstream("WordDocument").read()
    finally:
        ole.close()
    return _salvage_text(raw)
```
Add the dispatch branch inside `extract_text`'s `try` (with the other `application/*` exact matches, before the calendar/text branches is fine; place after `_XLS_MIME`):
```python
        if mime == "application/msword":
            return _doc_text(data)
```

- [ ] **Step 6: Run, verify PASS** (and full extract suite).
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 7: Commit.**
```bash
git add src/mboxviewer/extract.py requirements.txt Dockerfile tests/test_extract.py
git commit -m "feat: legacy .doc extraction (antiword + OLE text salvage)"
```

---

## Task 3: TNEF extraction + inner-attachment enumeration

**Files:** Modify `src/mboxviewer/extract.py`; Modify `tests/test_extract.py`.

- [ ] **Step 1: Failing tests — add to `tests/test_extract.py`:**
```python
import struct
from tnefparse import TNEF as _TNEF


def _build_tnef(body_text, files):
    def attr(level, att, data):
        return struct.pack("<BII", level, att, len(data)) + data + struct.pack("<H", sum(data) & 0xFFFF)
    out = struct.pack("<I", 0x223E9F78) + struct.pack("<H", 0x0001)
    out += attr(0x01, _TNEF.ATTBODY, body_text.encode() + b"\x00")
    for name, data in files:
        out += attr(0x02, _TNEF.ATTATTACHRENDDATA, b"\x00" * 16)
        out += attr(0x02, _TNEF.ATTATTACHTITLE, name.encode() + b"\x00")
        out += attr(0x02, _TNEF.ATTATTACHDATA, data)
    return out


def test_extract_tnef_text():
    from mboxviewer.extract import extract_text
    raw = _build_tnef("Hello from Outlook", [("report.txt", b"INNER REPORT 999")])
    out = extract_text("winmail.dat", "application/ms-tnef", raw)
    assert "Contained files" in out and "report.txt" in out
    assert "Hello from Outlook" in out and "INNER REPORT 999" in out


def test_iter_tnef_attachments():
    from mboxviewer.extract import iter_tnef_attachments
    raw = _build_tnef("body", [("report.txt", b"DATA1"), ("p.pdf", b"%PDF-junk")])
    items = iter_tnef_attachments(raw)
    assert [(n, m) for (n, m, _b) in items] == [("report.txt", "text/plain"), ("p.pdf", "application/pdf")]
    assert items[0][2] == b"DATA1"


def test_extract_tnef_garbage_empty():
    from mboxviewer.extract import extract_text
    assert extract_text("winmail.dat", "application/ms-tnef", b"not tnef") == ""
```

- [ ] **Step 2: Run, verify FAIL.**
Run: `.venv/bin/pytest tests/test_extract.py -k tnef -v`

- [ ] **Step 3: `extract.py`.** Add `import mimetypes` near the top imports. Add helpers (after `_doc_text`):
```python
def iter_tnef_attachments(data: bytes):
    """[(name, mime, bytes)] for each inner attachment of a TNEF blob."""
    from tnefparse import TNEF
    tnef = TNEF(data, do_checksum=False)
    out = []
    for a in tnef.attachments:
        name = (a.name or "").strip() or "attachment"
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out.append((name, mime, a.data))
    return out


def _tnef_text(data: bytes) -> str:
    from tnefparse import TNEF
    tnef = TNEF(data, do_checksum=False)
    parts = []
    inner = list(iter_tnef_attachments(data))
    if inner:
        parts.append("Contained files: " + ", ".join(n for (n, _m, _b) in inner))
    body = tnef.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if body and body.strip():
        parts.append(body.replace("\x00", "").strip())
    elif tnef.htmlbody:
        hb = tnef.htmlbody
        if isinstance(hb, bytes):
            hb = hb.decode("utf-8", "replace")
        parts.append(html_to_text(hb))
    for name, mime, blob in inner:
        sub = extract_text(name, mime, blob)
        if sub and sub.strip():
            parts.append(sub.strip())
    return "\n\n".join(p for p in parts if p).strip()
```
Add the dispatch branch inside `extract_text`'s `try` (with the `application/*` matches):
```python
        if mime == "application/ms-tnef":
            return _tnef_text(data)
```

- [ ] **Step 4: Run, verify PASS** (and full extract suite).
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 5: Commit.**
```bash
git add src/mboxviewer/extract.py tests/test_extract.py
git commit -m "feat: TNEF (winmail.dat) text extraction and inner-attachment enumeration"
```

---

## Task 4: API — TNEF inner-file list + download

**Files:** Modify `src/mboxviewer/api.py`; Modify `tests/test_api.py`.

- [ ] **Step 1: Failing test — add to `tests/test_api.py`:**
```python
def test_tnef_inner_endpoints(tmp_path):
    import io, struct
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    from tnefparse import TNEF
    def attr(level, att, data):
        return struct.pack("<BII", level, att, len(data)) + data + struct.pack("<H", sum(data) & 0xFFFF)
    raw = struct.pack("<I", 0x223E9F78) + struct.pack("<H", 1)
    raw += attr(0x01, TNEF.ATTBODY, b"body\x00")
    raw += attr(0x02, TNEF.ATTATTACHRENDDATA, b"\x00" * 16)
    raw += attr(0x02, TNEF.ATTATTACHTITLE, b"report.txt\x00")
    raw += attr(0x02, TNEF.ATTATTACHDATA, b"INNER-BYTES")
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(raw, maintype="application", subtype="ms-tnef", filename="winmail.dat")
    m.add_attachment(b"plain", maintype="text", subtype="plain", filename="n.txt")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "t.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    mid = c.get("/api/messages").json()["messages"][0]["id"]
    listing = c.get(f"/api/messages/{mid}/attachments/0/inner").json()
    assert listing["files"][0]["name"] == "report.txt"
    assert listing["files"][0]["mime"] == "text/plain"
    assert listing["files"][0]["size"] == len(b"INNER-BYTES")
    r = c.get(f"/api/messages/{mid}/attachments/0/inner/0")
    assert r.content == b"INNER-BYTES"
    assert r.headers["x-content-type-options"] == "nosniff"
    # a non-TNEF attachment lists nothing; bad inner index 404s
    assert c.get(f"/api/messages/{mid}/attachments/1/inner").json()["files"] == []
    assert c.get(f"/api/messages/{mid}/attachments/0/inner/9").status_code == 404
```

- [ ] **Step 2: Run, verify FAIL** (404 — routes don't exist).
Run: `.venv/bin/pytest tests/test_api.py::test_tnef_inner_endpoints -v`

- [ ] **Step 3: `api.py`.** Add `from .extract import extract_text, iter_tnef_attachments` (extend the existing `from .extract import extract_text` line). Add these routes immediately after the `attachment` route (before `/api/archive/start`):
```python
    def _tnef_inner(message_id, idx):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                if mime != "application/ms-tnef":
                    return []
                try:
                    return iter_tnef_attachments(payload)
                except Exception:
                    return []
        raise HTTPException(404, "attachment not found")

    @app.get("/api/messages/{message_id}/attachments/{idx}/inner")
    def tnef_inner_list(message_id: int, idx: int):
        inner = _tnef_inner(message_id, idx)
        return {"files": [{"k": k, "name": name, "mime": mime, "size": len(blob)}
                          for k, (name, mime, blob) in enumerate(inner)]}

    @app.get("/api/messages/{message_id}/attachments/{idx}/inner/{k}")
    def tnef_inner_file(message_id: int, idx: int, k: int, inline: bool = False):
        inner = _tnef_inner(message_id, idx)
        if k < 0 or k >= len(inner):
            raise HTTPException(404, "inner attachment not found")
        name, mime, blob = inner[k]
        safe_inline = inline and mime in _SAFE_INLINE_MIMES
        return Response(
            content=blob, media_type=mime,
            headers={
                "Content-Disposition": _content_disposition(name, inline=safe_inline),
                "X-Content-Type-Options": "nosniff",
            })
```

- [ ] **Step 4: Run, verify PASS** (new + full api suite).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Run full suite.**
Run: `.venv/bin/pytest -q`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: API to list and download TNEF inner attachments"
```

---

## Task 5: Frontend — TNEF viewer in the Files-mode reader

**Files:** Modify `src/mboxviewer/static/app.js`. No unit tests (static); verified in Task 6.

- [ ] **Step 1: `app.js`.** In `openFile`, add a TNEF branch BEFORE the final `showOnlyPane(readerText)` text fallback (i.e. after the csv branch). Insert:
```javascript
  if (m === "application/ms-tnef" || name.endsWith(".dat")) {
    showOnlyPane(readerText);
    readerText.textContent = "Loading…";
    let contained = "";
    try {
      const list = await getJSON(`/api/messages/${mid}/attachments/${idx}/inner`);
      if (list.files && list.files.length) {
        contained = " · Contained: " + list.files.map(f =>
          `<a href="/api/messages/${mid}/attachments/${idx}/inner/${f.k}" download>${escapeHtml(f.name)} (${humanSize(f.size)})</a>`
        ).join(" ");
      }
    } catch (e) { /* ignore — still show text */ }
    readerAtt.innerHTML += contained;
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerText.textContent = (d.text && d.text.trim()) ? d.text : "No extractable content.";
    } catch (err) {
      readerText.textContent = "Failed to load: " + err.message;
    }
    return;
  }
```
(Note: `readerAtt.innerHTML` already holds the Download + Open-email buttons set at the top of
`openFile`; this appends the contained-file links. All file names are `escapeHtml`'d.)

- [ ] **Step 2: Verify.**
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `node --check src/mboxviewer/static/app.js && echo ok` (if node present)
Run: `grep -n "ms-tnef\|inner/" src/mboxviewer/static/app.js` — report.
Run: `.venv/bin/pytest -q` (unchanged, 12x passed).

- [ ] **Step 3: Commit.**
```bash
git add src/mboxviewer/static/app.js
git commit -m "feat: TNEF reader — unwrapped text plus contained-file download links"
```

---

## Task 6: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Build a sample mbox with a winmail.dat (TNEF) and a vCard, and run the viewer.**
Terminal 1:
```bash
rm -rf /tmp/tv.db* /tmp/tvarch /tmp/tv.mbox 2>/dev/null
.venv/bin/python - <<'PY'
import io, struct
from email.message import EmailMessage
from email.generator import BytesGenerator
from tnefparse import TNEF
def attr(level, att, data):
    return struct.pack("<BII", level, att, len(data)) + data + struct.pack("<H", sum(data)&0xFFFF)
raw = struct.pack("<I",0x223E9F78)+struct.pack("<H",1)
raw += attr(0x01, TNEF.ATTBODY, b"Please find the attached report.\x00")
raw += attr(0x02, TNEF.ATTATTACHRENDDATA, b"\x00"*16)
raw += attr(0x02, TNEF.ATTATTACHTITLE, b"report.txt\x00")
raw += attr(0x02, TNEF.ATTATTACHDATA, b"QUARTERLY NUMBERS 4200")
vcf=("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice Smith\r\nORG:Acme\r\nTITLE:Engineer\r\n"
     "EMAIL:alice@example.com\r\nTEL:+1-555-1234\r\nEND:VCARD\r\n").encode()
def email(sub, atts):
    m=EmailMessage(); m["Subject"]=sub; m["From"]="a@x.com"; m["To"]="b@x.com"
    m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
    m.set_content("body"); m.add_alternative("<p>x</p>", subtype="html")
    for fn,mt,st,d in atts: m.add_attachment(d, maintype=mt, subtype=st, filename=fn)
    return m
msgs=[email("Outlook msg",[("winmail.dat","application","ms-tnef",raw)]),
      email("Contact",[("alice.vcf","text","x-vcard",vcf)])]
with open("/tmp/tv.mbox","wb") as f:
    for m in msgs:
        d=io.BytesIO(); BytesGenerator(d).flatten(m); d=d.getvalue()
        f.write(b"From - x\n"+d+(b"" if d.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/tv.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/tv.mbox INDEX_PATH=/tmp/tv.db ARCHIVE_DIR=/tmp/tvarch \
  HOST=127.0.0.1 PORT=8140 .venv/bin/python -m mboxviewer.main
```
Terminal 2 — API smoke:
```bash
curl -s "http://127.0.0.1:8140/api/filetypes"                                  # Contacts present
# vCard text:
curl -s "http://127.0.0.1:8140/api/files?category=Contacts" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])" | { read M I; curl -s "http://127.0.0.1:8140/api/files/$M/$I/text"; }
# TNEF: find the winmail.dat (Other category), list inner, download inner 0
curl -s "http://127.0.0.1:8140/api/files?category=Other" | .venv/bin/python -c "import sys,json;f=[x for x in json.load(sys.stdin)['files'] if x['mime']=='application/ms-tnef'][0];print(f['message_id'],f['idx'])" | { read M I; echo INNER:; curl -s "http://127.0.0.1:8140/api/messages/$M/attachments/$I/inner"; echo; echo DOWNLOAD:; curl -s "http://127.0.0.1:8140/api/messages/$M/attachments/$I/inner/0"; echo; echo TEXT:; curl -s "http://127.0.0.1:8140/api/files/$M/$I/text"; }
```

- [ ] **Step 2: Browser check** at http://127.0.0.1:8140 (Files mode):
  - **Contacts** → `alice.vcf` → labeled `Name: Alice Smith / Organization: Acme / Email: … / Phone: …`.
  - **Other** → `winmail.dat` → reader shows the unwrapped text ("Please find the attached report.",
    "Contained files: report.txt", "QUARTERLY NUMBERS 4200") and a **Contained: report.txt (… )**
    download link that fetches the inner bytes.
  Stop the server; `rm -rf /tmp/tv.mbox /tmp/tv.db* /tmp/tvarch`.

- [ ] **Step 3: Redeploy the real container** (Dockerfile changed → rebuild installs antiword; index reused).
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Then at http://localhost:9000: a real `winmail.dat` shows its contained files (downloadable) + text; a
real `.doc` shows antiword-extracted text; a real vCard shows under **Contacts**. Confirm no re-index
(serves immediately).
NOTE: content search for TNEF/.doc/vCard reflects a future re-index; viewing works immediately.

---

## Self-Review Notes

- **Spec coverage:** vCard + Contacts category (Task 1); legacy .doc antiword+salvage (Task 2);
  TNEF text + inner enumeration (Task 3); TNEF inner-file API with disposition policy (Task 4);
  TNEF reader UI (Task 5); e2e + redeploy + FTS note (Task 6).
- **Type/name consistency:** `iter_tnef_attachments` returns `[(name, mime, bytes)]` used by both
  `_tnef_text` and the API; `_inner` list shape `{files:[{k,name,mime,size}]}` matches the JS reader;
  `_VCARD_MIMES`/`_CONTACTS` and `_CALENDAR_MIMES`/`_CALENDAR` cross-referenced; new endpoints reuse
  `_content_disposition`/`_SAFE_INLINE_MIMES`/`read_message`/`iter_attachments` (contract #1 & #8
  intact).
- **Security:** inner-file disposition allowlisted + `nosniff`; contained-file names `escapeHtml`'d on
  the innerHTML path; extracted text via `textContent`; antiword sandboxed to a temp file + timeout.
- **No placeholders:** all code/tests verified-from-prototypes (TNEF builder, salvage, vCard) are complete.
