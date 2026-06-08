# Power Features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** `.eml` export, integrity report, search snippets/highlight + filters/sort, image gallery, shrink "Other" (Enclosures/Signatures), bulk zip export, keyboard shortcuts.

**Spec:** `docs/superpowers/specs/2026-06-08-power-features-design.md`

**Re-index:** Tasks 2 & 3 change categorization + add a `messages.preview` column → bump `SCHEMA_VERSION` once (Task 3); the guard re-indexes on deploy.

---

## Task 1: reader — raw `.eml` bytes

**Files:** `src/mboxviewer/reader.py`; `tests/test_reader.py`.

- [ ] Failing test: `read_message_bytes(path, offset, length)` returns the original RFC-822 bytes — the leading `From …` line removed and mboxrd `>From`→`From` un-escaped. Use the `sample_mbox` (read a span from `store`/`all_message_spans` or re-derive via `iter_message_spans`), assert the bytes start with a header (e.g. `b"Subject:"`/`b"From:"`) and contain no leading `From ` envelope line.
- [ ] Implement: refactor `read_message` so the byte-prep is a function:
```python
def read_message_bytes(path, offset, length):
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(length)
    nl = raw.find(b"\n")
    if raw.startswith(b"From ") and nl != -1:
        raw = raw[nl + 1:]
    return re.sub(rb"(?m)^>(>*From )", rb"\1", raw)
```
and `read_message` becomes `return email.message_from_bytes(read_message_bytes(path, offset, length), policy=…)` (keep the existing policy/import).
- [ ] Run `tests/test_reader.py -v`; commit: `feat: reader.read_message_bytes for raw .eml export`.

---

## Task 2: filetypes — Enclosures + Signatures (shrink Other)

**Files:** `src/mboxviewer/filetypes.py`; `tests/test_filetypes.py`.

- [ ] Failing test: `category_for_mime("application/ms-tnef") == "Enclosures"`;
  `category_for_mime("application/pkcs7-signature") == "Signatures"` and `application/x-pkcs7-signature`,
  `application/pgp-signature` too; both in `CATEGORY_ORDER`; `category_for("application/octet-stream","x.dat")=="Enclosures"`, `("application/octet-stream","s.p7s")=="Signatures"`.
- [ ] Implement:
  - `CATEGORY_ORDER`: insert `"Enclosures", "Signatures"` after `"Archives"`.
  - Add sets `_ENCLOSURES = {"application/ms-tnef","application/ms-tnefx"}` and
    `_SIGNATURES = {"application/pkcs7-signature","application/x-pkcs7-signature","application/pgp-signature","application/pkcs7-mime"}`; in `category_for_mime` check them (before the `_ARCHIVES`/`text` fallbacks).
  - `_EXT_CATEGORY`: add `Enclosures: ".dat"`, `Signatures: ".p7s .p7m .asc .sig"`.
- [ ] Run filetypes tests; commit: `feat: Enclosures + Signatures categories`.

---

## Task 3: store + indexer — preview, filters/sort, export query, integrity; SCHEMA_VERSION bump

**Files:** `src/mboxviewer/store.py`, `src/mboxviewer/indexer.py`; `tests/test_store.py`, `tests/test_indexer.py`.

- [ ] Failing tests (store): a built index returns `preview` on message rows; `list_messages(..., from_q="carol")` filters by sender; `list_messages(..., has_attachment=True)` only returns messages with attachments; `list_messages(..., sort="date_asc")` reverses order; `search("…", from_q=…/date_from=…)` applies filters; `list_files_for_export("Documents", None, 100)` returns `(message_id, idx, filename, mime, size)` rows; `integrity()` reads `meta` counts.
- [ ] Failing tests (indexer): after `build_index`, a message row has a non-empty `preview`; with a monkeypatched extractor that raises on one message, `store.get_meta("skipped_count")` ≥ "1" and `integrity()["skipped"]` ≥ 1.
- [ ] Implement `store.py`:
  - `SCHEMA`: add `preview TEXT` to the `messages` table; `create_schema` ALTER-adds `preview` (guarded like `category`). **Bump `SCHEMA_VERSION`** to the next integer.
  - `add_message(..., preview=None)` — add the column to the INSERT.
  - Extend `list_messages(self, label, limit, offset, date_from=None, date_to=None, from_q=None, has_attachment=False, sort="date_desc")`: build a dynamic WHERE (label join unchanged) with bound params: `m.date >= ?` (date_from), `m.date <= ?` (date_to, inclusive — append `"￿"` or use `< date_to+1day`; simplest: `m.date <= ?` with the caller passing an end-of-day or we compare on the date prefix — use `substr(m.date,1,10) BETWEEN ? AND ?` when both given), `m.from_addr LIKE ?` (from_q, escaped), and `EXISTS (SELECT 1 FROM attachments a WHERE a.message_id=m.id)` (has_attachment). `ORDER BY` via a fixed map `{"date_desc":"m.date DESC","date_asc":"m.date ASC"}` (never interpolate the raw value). Keep pagination.
  - Extend `search(...)` with the same optional filters/sort (default `ORDER BY rank`; when `sort` is given, use the date map instead of rank). Keep the `OperationalError` guard.
  - `list_files_for_export(self, category, query, limit)`: like `list_files_by_category` but `SELECT a.message_id, a.idx, a.filename, a.mime, a.size` with the category/query WHERE, `LIMIT ?`.
  - `integrity(self)`: `{"indexed": int(get_meta("indexed_count") or 0), "skipped": int(get_meta("skipped_count") or 0), "sample": json.loads(get_meta("skipped_sample") or "[]")}`.
- [ ] Implement `indexer.py`:
  - Compute `preview` = first 300 chars of a whitespace-collapsed `_body_text(msg)`; pass to `add_message`.
  - Count `skipped`; collect up to 25 `{"offset": offset, "reason": str(exc)[:200]}`; after the loop `set_meta("indexed_count", str(count))`, `set_meta("skipped_count", str(skipped))`, `set_meta("skipped_sample", json.dumps(sample))` (add `import json`).
- [ ] Run store + indexer tests + full suite; commit: `feat: message preview, search filters/sort, export query, integrity tracking`.

---

## Task 4: api — raw/.eml, integrity, bulk export, filters/sort/preview

**Files:** `src/mboxviewer/api.py`; `tests/test_api.py`.

- [ ] Failing tests:
  - `GET /api/messages/{id}/raw` → 200, `content-type` starts `message/rfc822`, `content-disposition` has `.eml`, body contains a header line.
  - `GET /api/integrity` → keys `indexed`, `skipped`, `sample`, `messages`.
  - `GET /api/files/export?category=Documents` → 200, `application/zip`; the bytes open as a zip whose names include the category's filenames.
  - `GET /api/search?q=…&from_q=…&sort=date_asc` returns filtered/ordered results and each row has `preview`.
- [ ] Implement:
  - Add `from .reader import read_message_bytes` (extend existing reader import). `import io, zipfile, json` as needed (some present).
  - `_msg_summary` includes `"preview": row["preview"]`.
  - `/api/messages` and `/api/search`: add `date_from, date_to, from_q: Optional[str] = None`, `has_attachment: bool = False`, `sort: str = "date_desc"` and pass to the store.
  - `@app.get("/api/messages/{message_id}/raw")`: `get_message_row`→404; `read_message_bytes`→ `Response(content=…, media_type="message/rfc822", headers={"Content-Disposition": _content_disposition(f"message-{message_id}.eml")})` (FileNotFoundError→503).
  - `@app.get("/api/integrity")`: `{**store.integrity(), "messages": store.message_count()}`.
  - `@app.get("/api/files/export")`: `category: Optional[str]=None, q: Optional[str]=None`. Resolve rows via `store.list_files_for_export(category or None, (q or '').strip() or None, 1000)`; if none → 404. Build a zip into a `tempfile.SpooledTemporaryFile(max_size=64*1024*1024)`:
    ```python
    import zipfile, tempfile, os as _os
    total = 0; CAP_BYTES = 1024**3
    spool = tempfile.SpooledTemporaryFile(max_size=64*1024*1024)
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            try:
                msg = read_message(settings.mbox_path, store.get_message_row(r["message_id"])["offset"],
                                   store.get_message_row(r["message_id"])["length"])
            except FileNotFoundError:
                raise HTTPException(503, "mbox file not available")
            for a_idx, filename, mime, payload in iter_attachments(msg):
                if a_idx == r["idx"]:
                    if total + len(payload) > CAP_BYTES: break
                    base = _os.path.basename(filename or "file"); 
                    name = f"{r['message_id']}-{base}"
                    zf.writestr(name, payload); total += len(payload)
                    break
    spool.seek(0)
    data = spool.read()
    label = (category or ("search-" + (q or "")).strip()) or "files"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": _content_disposition(f"mbox-{label}.zip")})
    ```
    (Optimization note: reading the message per row is O(n) opens; acceptable for the ≤1000 cap. If trivial, batch by message_id.)
- [ ] Run api tests + full suite; commit: `feat: /api/messages/{id}/raw, /api/integrity, /api/files/export; search filters/sort`.

---

## Task 5: Frontend

**Files:** `src/mboxviewer/static/{index.html,app.js,style.css}`. Verified in Task 7.

- [ ] **5a — result snippets + highlight + preview + .eml link.**
  - `appendRows` (folders/search rows): under subject add `<div class="preview">` = the row's `preview`,
    and **highlight** the active `currentQuery` terms in subject+preview: a `highlight(text)` helper that
    `escapeHtml`s then wraps each whitespace-split term (regex-escaped, case-insensitive) in `<mark>`.
  - `openMessage` reader header: add a **Download .eml** link → `/api/messages/${id}/raw`.
- [ ] **5b — search filters bar.** Add a collapsible row under `#searchbar`: `date from`/`to` (`type=date`),
  `sender contains` (`type=search`), `☑ has attachment`, `sort` (`<select>`: Newest/Oldest). State vars
  `filterFrom,filterTo,filterSender,filterHasAtt,sortOrder`; `pageUrl` (folders/search branch) appends
  `date_from,date_to,from_q,has_attachment,sort`; any change calls `reload()`. (Files mode unaffected.)
- [ ] **5c — image gallery.** When `browseMode==="files" && activeCategory==="Images"`, `appendRows`
  renders each item as a grid cell: `<img loading="lazy" src="/api/messages/${it.message_id}/attachments/${it.idx}?inline=1">` inside `#message-list` with class `gallery`; clicking opens `openFile(...)`. Toggle a
  `.gallery` class on `#message-list` for the grid CSS. Non-Images categories unchanged.
- [ ] **5d — bulk export.** A **Download all** button shown in Files mode when `activeCategory||currentQuery`:
  `window.location = "/api/files/export?" + params(category,q)` (browser download).
- [ ] **5e — keyboard shortcuts.** Extend the keydown handler: `/` (not in input) → focus `#q` + preventDefault;
  `j`/`k` → same as ArrowDown/ArrowUp; `Escape` in `#q` → blur. Keep ignoring arrows/typing in inputs.
- [ ] **5f — integrity footer.** On load, `getJSON('/api/integrity')` → set a footer span
  `Index: N indexed · M skipped` (skipped as a `title=` listing sample reasons; if M>0 add an `err`-ish hint).
- [ ] **index.html:** add the filters bar container, the integrity footer span, the Download-all button, the `.eml` is built in JS.
- [ ] **style.css:** `.preview` (muted, ellipsized), `mark` (subtle highlight), `#message-list.gallery` (grid; `img` thumbnails square-ish, object-fit cover), filters bar layout, Download-all button.
- [ ] Verify braces balanced + `node --check`; commit: `feat: snippets/highlight, filters, gallery, bulk export, shortcuts, integrity footer`.

---

## Task 6: README

**Files:** `README.md`.

- [ ] Rewrite the **features** list to reflect today: Folders + **Files** browse modes; categories
  (Documents/Spreadsheets/Presentations/Images/Archives/Calendar/Contacts/Enclosures/Signatures/Media/Other)
  with extension-fallback; viewers/players (PDF inline, image preview & **gallery**, audio/video with the
  WMA notice, CSV/spreadsheet **tables**, ICS calendar, vCard, **TNEF/winmail.dat unwrap**, **archive
  listings**, legacy `.doc`/`.ppt` via antiword/catppt, pptx/xlsx/xls); **full-text search** over bodies +
  attachment text + archive/inner names, with **snippets/highlight + filters/sort**; **infinite scroll**;
  remote-image **archiving**; **`.eml` export** + **bulk zip export**; **integrity report**;
  **schema-version guard** (auto re-index); SVG favicon; keyboard shortcuts.
- [ ] Update **Using the viewer** (Folders/Files tabs, gallery, filters, shortcuts: `/`,`j`/`k`,`Esc`,arrows).
- [ ] Add a **## Roadmap** section from the spec's Roadmap list.
- [ ] Commit: `docs: refresh README features + add roadmap`.

---

## Task 7: e2e + redeploy

- [ ] Local sample mbox (a few messages with attachments incl. a winmail.dat, a couple images, a `.p7s`):
  verify `/api/integrity`, `/api/messages/{id}/raw` (.eml opens), `/api/files/export` (zip), search with a
  `from_q`/`sort`, Enclosures/Signatures categories. Browser: snippet highlight, filters, gallery grid,
  Download-all, `.eml` link, shortcuts (`/`,`j`/`k`,`Esc`), integrity footer.
- [ ] Redeploy: `docker rm -f mbox-mbox-viewer-1; ./run.sh` — the `SCHEMA_VERSION` bump makes the guard
  re-index automatically (no manual clear). After indexing: Other shrinks (Enclosures≈winmail.dat,
  Signatures≈p7s); a real `.eml` opens; bulk export of a small category works; search snippets/filters work.

---

## Self-Review Notes
- One `SCHEMA_VERSION` bump (Task 3) covers the preview column + new categories → one guard-driven re-index.
- Security: `.eml`/zip reuse offset read + `iter_attachments` (contract #1); zip names `basename`-sanitized + caps; highlight/preview escape-then-`<mark>` in the main doc only; gallery uses the allowlisted inline endpoint; sort via fixed map, filters bound params.
- No new Python deps (stdlib `zipfile`/`tempfile`/`json`).
