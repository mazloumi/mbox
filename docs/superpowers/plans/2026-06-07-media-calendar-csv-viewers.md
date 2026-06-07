# In-Browser Viewers & Players (Tier 1 + Tier 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HTML5 audio/video players, an ICS calendar viewer (+ categorization fix), BMP preview, and a CSV table viewer to the Files-mode reader.

**Architecture:** Backend: extend `_SAFE_INLINE_MIMES` (non-scriptable media + bmp), map calendar MIMEs to the Calendar category, and add ICS + text-like extraction to `extract.py`. Frontend: three new reader panes (audio/video/table), a `showOnlyPane()` helper that centralizes exclusivity and stops media, and `openFile` MIME dispatch incl. a dependency-free CSV→`<table>` renderer.

**Tech Stack:** Python 3.9, FastAPI, SQLite, vanilla JS. No new dependencies. Tests: pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-06-07-media-calendar-csv-viewers-design.md`

---

## File Structure

```
src/mboxviewer/filetypes.py      # MODIFY — Calendar MIME set (incl application/ics)
src/mboxviewer/extract.py        # MODIFY — _ics_text + text-like application/* branch
src/mboxviewer/api.py            # MODIFY — _SAFE_INLINE_MIMES (media + bmp)
src/mboxviewer/static/index.html # MODIFY — #reader-audio / #reader-video / #reader-table
src/mboxviewer/static/app.js     # MODIFY — refs, showOnlyPane, openFile dispatch, CSV
src/mboxviewer/static/style.css  # MODIFY — audio/video/table styles
tests/test_filetypes.py          # MODIFY — calendar categorization
tests/test_extract.py            # MODIFY — ICS + text-like
tests/test_api.py                # MODIFY — media/bmp inline; html still forced
```

---

## Task 1: extract + filetypes — ICS viewer, text-like, Calendar categorization

**Files:** Modify `src/mboxviewer/extract.py`, `src/mboxviewer/filetypes.py`; Modify `tests/test_filetypes.py`, `tests/test_extract.py`.

- [ ] **Step 1: Write failing filetypes test — add to `tests/test_filetypes.py`:**
```python
def test_calendar_mimes():
    for m in ["text/calendar", "application/ics", "text/x-vcalendar"]:
        assert category_for_mime(m) == "Calendar", m
```

- [ ] **Step 2: Write failing extract tests — add to `tests/test_extract.py`:**
```python
def test_extract_ics_event():
    from mboxviewer.extract import extract_text
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        "SUMMARY:Project Kickoff\r\n"
        "DTSTART:20240115T090000Z\r\nDTEND:20240115T100000Z\r\n"
        "LOCATION:Room 4\r\n"
        "ORGANIZER:mailto:alice@example.com\r\n"
        "ATTENDEE:mailto:bob@example.com\r\n"
        "DESCRIPTION:Discuss the plan\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    out = extract_text("invite.ics", "application/ics", ics.encode())
    assert "Project Kickoff" in out and "Room 4" in out
    assert "alice@example.com" in out and "bob@example.com" in out
    assert "2024-01-15" in out  # DTSTART is pretty-printed


def test_extract_ics_unfolds_long_lines():
    from mboxviewer.extract import extract_text
    ics = (
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Quarterly planning and budget\r\n review session\r\n"
        "END:VEVENT\r\n"
    )
    out = extract_text("x.ics", "text/calendar", ics.encode())
    assert "Quarterly planning and budget review session" in out


def test_extract_textlike_application_types():
    from mboxviewer.extract import extract_text
    assert "hello" in extract_text("a.json", "application/json", b'{"k": "hello"}')
    assert "<note>" in extract_text("a.xml", "application/xml", b"<note>hi</note>")


def test_extract_ics_no_event_returns_empty():
    from mboxviewer.extract import extract_text
    assert extract_text("x.ics", "text/calendar", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n") == ""
```

- [ ] **Step 3: Run tests, verify they FAIL.**
Run: `.venv/bin/pytest tests/test_filetypes.py::test_calendar_mimes tests/test_extract.py -k "ics or textlike" -v`

- [ ] **Step 4: Modify `src/mboxviewer/filetypes.py`.**
Add a module-level set near the other `_…` sets:
```python
_CALENDAR = {"text/calendar", "application/ics", "text/x-vcalendar"}
```
Replace the line:
```python
    if m == "text/calendar":
        return "Calendar"
```
with:
```python
    if m in _CALENDAR:
        return "Calendar"
```

- [ ] **Step 5: Modify `src/mboxviewer/extract.py`.**
Add module-level constants near `_DOCX_MIME`:
```python
_CALENDAR_MIMES = {"text/calendar", "application/ics", "text/x-vcalendar"}
_TEXTLIKE_APP_MIMES = {
    "application/json", "application/xml",
    "application/x-yaml", "application/yaml",
}
```
Add `import re` to the top of the file (next to `import io`).
Add these helpers (e.g. after `_xls_text`):
```python
def _ics_unescape(v: str) -> str:
    return (v.replace("\\N", "\n").replace("\\n", "\n")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _ics_when(v: str) -> str:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$", v.strip())
    if not m:
        return v
    y, mo, d, h, mi, _s, z = m.groups()
    if h is None:
        return "%s-%s-%s" % (y, mo, d)
    return "%s-%s-%s %s:%s%s" % (y, mo, d, h, mi, " UTC" if z else "")


def _ics_text(data: bytes) -> str:
    raw = data.decode("utf-8", "replace")
    # RFC 5545 line unfolding: a line starting with space/tab continues the previous one.
    lines = []
    for line in raw.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    events = []
    cur = None
    for line in lines:
        key_line = line.strip()
        if key_line == "BEGIN:VEVENT":
            cur = {"attendees": []}
        elif key_line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
                cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";", 1)[0].upper()
            if key == "SUMMARY":
                cur["summary"] = _ics_unescape(value)
            elif key == "DTSTART":
                cur["start"] = _ics_when(value)
            elif key == "DTEND":
                cur["end"] = _ics_when(value)
            elif key == "LOCATION":
                cur["location"] = _ics_unescape(value)
            elif key == "ORGANIZER":
                cur["organizer"] = value.replace("mailto:", "").replace("MAILTO:", "")
            elif key == "ATTENDEE":
                cur["attendees"].append(value.replace("mailto:", "").replace("MAILTO:", ""))
            elif key == "DESCRIPTION":
                cur["description"] = _ics_unescape(value)
    out = []
    for e in events:
        if e.get("summary"):
            out.append("Summary: " + e["summary"])
        when = e.get("start", "")
        if e.get("end"):
            when = (when + " → " + e["end"]) if when else e["end"]
        if when:
            out.append("When: " + when)
        if e.get("location"):
            out.append("Location: " + e["location"])
        if e.get("organizer"):
            out.append("Organizer: " + e["organizer"])
        if e.get("attendees"):
            out.append("Attendees: " + ", ".join(e["attendees"]))
        if e.get("description"):
            out.append("Description: " + e["description"])
        out.append("")
    return "\n".join(out).strip()
```
Add dispatch branches inside `extract_text`'s `try`, **before** the `text/html` branch:
```python
        if mime in _CALENDAR_MIMES:
            return _ics_text(data)
        if mime in _TEXTLIKE_APP_MIMES:
            return data.decode("utf-8", "replace")
```

- [ ] **Step 5b: Run tests, verify they PASS** (full extract + filetypes suites).
Run: `.venv/bin/pytest tests/test_extract.py tests/test_filetypes.py -v`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/extract.py src/mboxviewer/filetypes.py tests/test_extract.py tests/test_filetypes.py
git commit -m "feat: ICS calendar extraction, text-like types, and Calendar categorization"
```

---

## Task 2: api — inline allowlist for safe media + bmp

**Files:** Modify `src/mboxviewer/api.py`; Modify `tests/test_api.py`.

- [ ] **Step 1: Write failing test — add to `tests/test_api.py`:**
```python
def test_inline_allows_safe_media_and_bmp(tmp_path):
    import io
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(b"ID3audio", maintype="audio", subtype="mpeg", filename="a.mp3")
    m.add_attachment(b"\x00\x00\x00mp4", maintype="video", subtype="mp4", filename="v.mp4")
    m.add_attachment(b"BM\x00\x00", maintype="image", subtype="bmp", filename="i.bmp")
    m.add_attachment(b"<b>x</b>", maintype="text", subtype="html", filename="e.html")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "m.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    mid = c.get("/api/messages").json()["messages"][0]["id"]
    # attachments are walk-ordered: 0=mp3, 1=mp4, 2=bmp, 3=html
    for idx in (0, 1, 2):
        r = c.get(f"/api/messages/{mid}/attachments/{idx}", params={"inline": "true"})
        assert r.headers["content-disposition"].startswith("inline"), idx
        assert r.headers["x-content-type-options"] == "nosniff"
    # text/html still forced to attachment (allowlist must not have widened unsafely)
    r = c.get(f"/api/messages/{mid}/attachments/3", params={"inline": "true"})
    assert r.headers["content-disposition"].startswith("attachment")
```

- [ ] **Step 2: Run test, verify it FAILS** (mp3/mp4/bmp currently forced to attachment).
Run: `.venv/bin/pytest tests/test_api.py::test_inline_allows_safe_media_and_bmp -v`

- [ ] **Step 3: Modify `src/mboxviewer/api.py`** — replace the `_SAFE_INLINE_MIMES` set with:
```python
_SAFE_INLINE_MIMES = frozenset({
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    # Non-scriptable media — safe to serve inline for <audio>/<video> playback.
    "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/aac",
    "audio/ogg", "audio/wav", "audio/webm",
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
})
```
Leave the comment above it intact (it already explains why text/html and svg are excluded).

- [ ] **Step 4: Run tests, verify they PASS** (new + the full api suite; the existing
  `test_inline_forced_to_attachment_for_unsafe_mime` must still pass).
Run: `.venv/bin/pytest tests/test_api.py -v`

- [ ] **Step 5: Run the full suite.**
Run: `.venv/bin/pytest -q`

- [ ] **Step 6: Commit.**
```bash
git add src/mboxviewer/api.py tests/test_api.py
git commit -m "feat: allowlist safe audio/video and bmp for inline serving"
```

---

## Task 3: Frontend — audio/video/table panes, showOnlyPane, openFile dispatch

**Files:** Modify `src/mboxviewer/static/index.html`, `static/app.js`, `static/style.css`.
No unit tests (static assets); verified in Task 4.

- [ ] **Step 1: `index.html` — add three panes.** Find `<img id="reader-image" hidden>` and add immediately after it:
```html
      <audio id="reader-audio" controls hidden></audio>
      <video id="reader-video" controls hidden></video>
      <div id="reader-table" hidden></div>
```

- [ ] **Step 2: `app.js` — add refs + pane list.** After `const readerImage = document.getElementById("reader-image");` add:
```javascript
const readerAudio = document.getElementById("reader-audio");
const readerVideo = document.getElementById("reader-video");
const readerTable = document.getElementById("reader-table");
const READER_PANES = [readerBody, readerPdf, readerText, readerImage, readerAudio, readerVideo, readerTable];
```

- [ ] **Step 3: `app.js` — add the `showOnlyPane` helper** (place just above `viewPdf`):
```javascript
// Show exactly one reader pane; hide the rest and stop/clear their content
// (so switching files stops audio/video and frees iframes).
function showOnlyPane(el) {
  for (const p of READER_PANES) {
    if (p === el) { p.hidden = false; continue; }
    p.hidden = true;
    if (p === readerAudio || p === readerVideo) {
      try { p.pause(); } catch (e) { /* ignore */ }
      p.removeAttribute("src"); p.load();
    } else if (p === readerPdf || p === readerImage) {
      p.removeAttribute("src");
    } else if (p === readerBody) {
      p.srcdoc = "";
    } else if (p === readerTable) {
      p.innerHTML = "";
    } else if (p === readerText) {
      p.textContent = "";
    }
  }
}
```

- [ ] **Step 4: `app.js` — rewrite `viewPdf`** (replace the whole function):
```javascript
function viewPdf(id, idx) {
  showOnlyPane(readerPdf);
  readerPdf.src = `/api/messages/${id}/attachments/${idx}?inline=1`;
}
```

- [ ] **Step 5: `app.js` — add the CSV helpers** (place just above `openFile`):
```javascript
function parseCsv(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else { q = false; } }
      else { field += c; }
    } else if (c === '"') { q = true; }
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") { field += c; }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.length > 1 || (r.length === 1 && r[0] !== ""));
}

function renderCsvTable(text) {
  const rows = parseCsv(text);
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
```

- [ ] **Step 6: `app.js` — rewrite `openFile`** (replace the whole function) to dispatch by MIME:
```javascript
async function openFile(mid, idx, filename, mime, size) {
  currentOpenId = mid;
  const m = (mime || "").toLowerCase();
  const name = (filename || "").toLowerCase();
  const inlineUrl = `/api/messages/${mid}/attachments/${idx}?inline=1`;
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(filename || "(no name)")}</div>
    <div class="meta">${escapeHtml(mime || "")} · ${humanSize(size)}</div>`;
  readerAtt.innerHTML =
    `<a href="/api/messages/${mid}/attachments/${idx}" download>Download</a>` +
    ` <button type="button" class="open-email" onclick="openEmailFromFile(${mid})">Open email</button>`;
  if (m.startsWith("image/")) { showOnlyPane(readerImage); readerImage.src = inlineUrl; return; }
  if (m.startsWith("audio/")) { showOnlyPane(readerAudio); readerAudio.src = inlineUrl; return; }
  if (m.startsWith("video/")) { showOnlyPane(readerVideo); readerVideo.src = inlineUrl; return; }
  if (m === "text/csv" || name.endsWith(".csv")) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim())
        ? renderCsvTable(d.text) : "No content.";
    } catch (err) {
      readerTable.textContent = "Failed to load file: " + err.message;
    }
    return;
  }
  showOnlyPane(readerText);
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

- [ ] **Step 7: `app.js` — update `openMessage` to use `showOnlyPane`.**
Replace these lines at the top of `openMessage`:
```javascript
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  readerBody.hidden = false;
  readerText.hidden = true;
  readerImage.hidden = true; readerImage.removeAttribute("src");
```
with:
```javascript
  currentOpenId = id;
  showOnlyPane(readerBody);
```

- [ ] **Step 8: `app.js` — update `setMode` to use `showOnlyPane`.**
Replace these four lines in `setMode`:
```javascript
  readerBody.srcdoc = ""; readerBody.hidden = (mode === "files");
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerText.hidden = true; readerText.textContent = "";
  readerImage.hidden = true; readerImage.removeAttribute("src");
```
with:
```javascript
  showOnlyPane(mode === "files" ? null : readerBody);
  readerBody.srcdoc = "";
```

- [ ] **Step 9: `style.css` — append pane styles:**
```css
#reader-audio { width: 100%; padding: 12px; box-sizing: border-box; }
#reader-video { flex: 1; min-height: 0; max-width: 100%; object-fit: contain; margin: 0;
  padding: 12px; align-self: center; background: #000; }
#reader-table { flex: 1; min-height: 0; overflow: auto; padding: 8px; }
#reader-table table { border-collapse: collapse; font: 12px/1.4 ui-monospace, Menlo, Consolas, monospace; }
#reader-table th, #reader-table td { border: 1px solid #ddd; padding: 2px 6px; text-align: left;
  white-space: nowrap; }
#reader-table th { position: sticky; top: 0; background: #f3f3f3; }
#reader-table .csv-note { font-family: sans-serif; color: #777; padding: 6px 2px; }
```

- [ ] **Step 10: Verify.**
Run: `.venv/bin/python -c "s=open('src/mboxviewer/static/app.js').read(); assert s.count('{')==s.count('}'), 'brace mismatch'; print('braces balanced')"`
Run: `grep -n "readerAudio\|readerVideo\|readerTable\|showOnlyPane" src/mboxviewer/static/app.js | head` — report it.
Run: `grep -rn "reader-audio\|reader-video\|reader-table" src/mboxviewer/static/index.html` — report it.
Run: `.venv/bin/pytest -q` (unchanged; frontend doesn't affect it).
Self-review: confirm the 7 panes are mutually exclusive (every show path goes through `showOnlyPane`), `node --check` style brace balance, and that `parseCsv` never throws on odd input (it's a single guarded loop).

- [ ] **Step 11: Commit.**
```bash
git add src/mboxviewer/static/index.html src/mboxviewer/static/app.js src/mboxviewer/static/style.css
git commit -m "feat: audio/video players, CSV table viewer, and showOnlyPane reader refactor"
```

---

## Task 4: End-to-end verification + redeploy

**Files:** none (verification only)

- [ ] **Step 1: Build a sample mbox with mp3 (audio), an ICS invite, and a CSV, and run the viewer.**
Terminal 1:
```bash
rm -rf /tmp/vw.db* /tmp/vwarch /tmp/vw.mbox 2>/dev/null
.venv/bin/python - <<'PY'
import io
from email.message import EmailMessage
from email.generator import BytesGenerator
def email(sub, atts):
    m=EmailMessage(); m["Subject"]=sub; m["From"]="a@x.com"; m["To"]="b@x.com"
    m["Date"]="Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"]="Inbox"
    m.set_content("body"); m.add_alternative("<p>see attached</p>", subtype="html")
    for fn,mt,st,data in atts: m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    return m
ics=("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Project Kickoff\r\n"
     "DTSTART:20240115T090000Z\r\nLOCATION:Room 4\r\n"
     "ORGANIZER:mailto:alice@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
csv=b"Region,Sales\nEMEA,4200\nAPAC,3100\n"
mp3=b"ID3" + b"\x00"*64  # not real audio; exercises serving/headers only
msgs=[email("Invite", [("kickoff.ics","application","ics",ics)]),
      email("Numbers", [("data.csv","text","csv",csv)]),
      email("Song", [("clip.mp3","audio","mpeg",mp3)])]
with open("/tmp/vw.mbox","wb") as f:
    for m in msgs:
        d=io.BytesIO(); BytesGenerator(d).flatten(m); d=d.getvalue()
        f.write(b"From - x\n"+d+(b"" if d.endswith(b"\n") else b"\n")+b"\n")
print("wrote /tmp/vw.mbox")
PY
PYTHONPATH=src MBOX_PATH=/tmp/vw.mbox INDEX_PATH=/tmp/vw.db ARCHIVE_DIR=/tmp/vwarch \
  HOST=127.0.0.1 PORT=8139 .venv/bin/python -m mboxviewer.main
```
Terminal 2 — API smoke test:
```bash
curl -s "http://127.0.0.1:8139/api/filetypes"                       # Calendar present (ics categorized)
curl -s "http://127.0.0.1:8139/api/files?category=Calendar"          # kickoff.ics listed
# ICS extracted text:
curl -s "http://127.0.0.1:8139/api/files?category=Calendar" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])" | { read M I; curl -s "http://127.0.0.1:8139/api/files/$M/$I/text"; }   # Summary/When/Location
# mp3 inline disposition:
curl -s "http://127.0.0.1:8139/api/files?category=Media" | .venv/bin/python -c "import sys,json;f=json.load(sys.stdin)['files'][0];print(f['message_id'],f['idx'])" | { read M I; curl -s -D - -o /dev/null "http://127.0.0.1:8139/api/messages/$M/attachments/$I?inline=1" | grep -i 'content-disposition\|content-type'; }
```

- [ ] **Step 2: Browser check** at http://127.0.0.1:8139 (Files mode):
  - **Media** → `clip.mp3` → an `<audio>` control appears (it won't play the fake bytes, but the
    control renders and Download works).
  - **Calendar** → `kickoff.ics` → labeled event text (`Summary: Project Kickoff`, `When: 2024-01-15…`,
    `Location: Room 4`).
  - **Spreadsheets** → `data.csv` → a **table** with header `Region | Sales` and rows EMEA/APAC.
  - Switch from the audio file to another file → audio stops. Open-email + Download present throughout.
  Stop the server; `rm -rf /tmp/vw.mbox /tmp/vw.db* /tmp/vwarch`.

- [ ] **Step 3: Redeploy the real container.** No new deps, but app code changed → rebuild; index reused.
```bash
docker rm -f mbox-mbox-viewer-1 2>/dev/null
./run.sh
```
Then at http://localhost:9000 (Files mode): **Calendar** now shows the `.ics` invites (previously in
Other) with labeled event text; a real audio/video file (if any) plays in the reader; a `.csv` renders
as a table; an image `.bmp` previews.
NOTE: ICS/text-like **content search** reflects a future re-index; viewing works immediately.

---

## Self-Review Notes

- **Spec coverage:** audio/video players (Task 2 allowlist + Task 3 panes/dispatch) ✓; ICS viewer +
  categorization fix (Task 1) ✓; BMP inline (Task 2) ✓; CSV table (Task 3) ✓; text-like extraction
  (Task 1) ✓; FTS note (Task 4) ✓.
- **Type/name consistency:** new ids `reader-audio`/`reader-video`/`reader-table` match index.html ↔
  app.js refs ↔ css; `showOnlyPane`/`READER_PANES`/`parseCsv`/`renderCsvTable` used consistently;
  every reader-show path routes through `showOnlyPane`, so the 7 panes stay exclusive and media stops
  on switch; `_SAFE_INLINE_MIMES` additions are non-scriptable; `_CALENDAR`/`_CALENDAR_MIMES` agree
  between filetypes and extract.
- **Security:** media/bmp inline are non-scriptable (no script context); text/html still forced to
  attachment (Task 2 test asserts it); CSV cells and all server strings `escapeHtml`'d; ICS/text via
  `textContent`. No new deps.
- **No placeholders:** every step has complete code/commands.
