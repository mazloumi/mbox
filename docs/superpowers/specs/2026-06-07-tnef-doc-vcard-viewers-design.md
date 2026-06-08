# TNEF Unwrapping, Legacy .doc, and vCard Viewer — Design

**Date:** 2026-06-07
**Status:** Approved

## Goal

Make the three biggest "locked-up" attachment classes in the mailbox usable:

1. **TNEF / `winmail.dat`** (`application/ms-tnef`, ~1,999 files) — Outlook's encapsulation wrapper.
   Decode it on demand to (a) extract the text of its inner attachments + message body for search
   and reading, and (b) list the **contained files with Download links** in the reader.
2. **Legacy `.doc`** (`application/msword`, ~998 files) — extract text (no extraction today; only
   `.docx` was handled).
3. **vCard** (`text/x-vcard`, ~308 files) — parse contact cards into labeled text, in a new
   **Contacts** category.

All on-demand via `extract_text` + small additive API/UI, consistent with the existing viewers.

## Decisions

- **TNEF is unwrapped at view time, not index time.** The `winmail.dat` stays a single top-level
  attachment (no change to `reader.iter_attachments` — contract #1 is untouched). Its inner files are
  exposed through new endpoints layered on the existing attachment `idx`. (Making inner files
  first-class entries in Files mode is a deliberate future option, not this change.)
- **Legacy `.doc` is best-effort.** Prefer `antiword` (added to the Docker image — accurate) and fall
  back to a pure-Python `olefile` "WordDocument" stream text-salvage so the dev venv (no antiword)
  still extracts something and the salvage logic is unit-testable. Quality varies; this is far better
  than the current zero.
- **vCard gets a new "Contacts" category** (mirrors the ICS/Calendar work). `.doc` stays in
  Documents; `winmail.dat` stays in **Other** (the viewer works wherever it is opened; a dedicated
  category is a future option).
- **No new runtime Python deps beyond** `olefile` and `tnefparse` (both pure-Python, verified on 3.9).
  `antiword` is a Debian package in the image only.

## Backend

### `requirements.txt`
Add `olefile==0.47`, `tnefparse==1.4.0`.

### `Dockerfile`
`apt-get update && apt-get install -y --no-install-recommends antiword && rm -rf /var/lib/apt/lists/*`
before `pip install` (for accurate `.doc` extraction in production).

### `filetypes.py` — Contacts category
- `CATEGORY_ORDER`: insert `"Contacts"` after `"Calendar"`.
- `category_for_mime`: map `text/x-vcard`, `text/vcard`, `application/vcard`, `text/directory` →
  `Contacts` (checked before the generic `text/*` → Documents fallback).

### `extract.py` — three extractors (all under the existing try/except → `""`)
- **vCard** (`_vcard_text`): unfold folded lines (same rule as ICS), then per `VCARD` emit labeled
  lines — `Name` (FN, or N joined), `Title`, `Organization`, `Email` (all), `Phone` (all),
  `Address`, `URL`. Values unescaped (reuse the ICS `\,`/`\n`/`\;`/`\\` unescape).
- **Legacy .doc** (`_doc_text`): `antiword` (if `shutil.which("antiword")`) via subprocess on a temp
  file → stdout text; else `_doc_salvage_ole` — `olefile` opens the OLE, reads the `WordDocument`
  stream, and `_salvage_text(raw)` decodes UTF-16LE and CP1252, keeps printable runs, collapses
  whitespace, and returns whichever has more alphabetic content. Non-OLE input → `""`.
- **TNEF** (`_tnef_text`): `tnefparse.TNEF(data, do_checksum=False)`; emit `Contained files: a, b…`,
  then the message body (`.body`, or `html_to_text(.htmlbody)`), then for each inner attachment
  recurse `extract_text(name, guessed_mime, inner.data)` so inner PDFs/docs/text are searchable.
  Plus a helper `iter_tnef_attachments(data) -> [(name, mime, bytes)]` (mime via
  `mimetypes.guess_type`) used by the API.
- Dispatch by exact MIME: `application/msword` → `_doc_text`; `application/ms-tnef` → `_tnef_text`;
  the vCard mimes → `_vcard_text` — all before the `text/html`/`text/` branches.

### `api.py` — TNEF inner-file endpoints
- `GET /api/messages/{id}/attachments/{idx}/inner` → walk to attachment `idx`; if its content type
  is `application/ms-tnef`, return `{files: [{k, name, size, mime}]}` (k = inner index from
  `iter_tnef_attachments`); 404 if message/attachment missing; `{files: []}` if not TNEF.
- `GET /api/messages/{id}/attachments/{idx}/inner/{k}` → serve inner file `k`'s bytes with the SAME
  disposition policy as the attachment route (`_content_disposition` + `_SAFE_INLINE_MIMES` +
  `nosniff`); 404 if out of range. Reuses `read_message`/`iter_attachments`/`iter_tnef_attachments`.

## Frontend (`static/`)

`openFile` gains a branch: when `mime === "application/ms-tnef"` (or filename ends `.dat`):
- `showOnlyPane(readerText)`; fetch `/text` → show the unwrapped body/inner text (or "No text").
- Fetch `/inner` → if it returns files, append to `#reader-attachments` a **Contained files** list:
  for each, `name (humanSize)` + a Download link to `.../inner/{k}` (names `escapeHtml`'d; the area is
  the non-sandboxed main document, so escaping is required). Download + Open email remain.

No new reader pane. vCard and `.doc` need **no** frontend change — they extract to text and render in
the existing text pane; vCard appears under the new Contacts category (the category list is
data-driven from `/api/filetypes`).

## Security
- TNEF inner-file serving reuses the existing allowlist + `nosniff` + disposition logic (contract #8):
  an inner `text/html`/`svg` is forced to `attachment`. Inner index `k` is an `int` path param.
- Contained-file names and all server strings rendered via `escapeHtml` on the innerHTML path;
  extracted text via `textContent`.
- `.doc` antiword runs on a temp file with a timeout; failures swallowed → `""`.

## Error handling
- Corrupt/borderline TNEF (`do_checksum=False`) → tnefparse best-effort; on exception `extract_text`
  returns `""` and `/inner` returns `{files: []}`.
- `.doc` that isn't valid OLE and has no antiword → `""` ("No extractable text").
- vCard with no card / odd input → `""`.

## Testing
- **filetypes:** `text/x-vcard`, `text/vcard`, `application/vcard` → Contacts; `Contacts` in CATEGORY_ORDER.
- **extract vCard:** a VCARD string (incl. a folded line and multiple EMAIL/TEL) → labeled Name/Email/Phone/Org.
- **extract .doc:** `_salvage_text` on synthetic UTF-16LE bytes with binary noise → the embedded words;
  `extract_text(.., application/msword, b"not ole")` → `""`.
- **extract TNEF:** build a minimal TNEF in-test (signature + ATTBODY + per-attachment
  ATTACHRENDDATA/ATTACHTITLE/ATTACHDATA using `tnefparse.TNEF.ATT*` codes) with a `report.txt`
  inner file → `_tnef_text` contains "Contained files", the body, and the inner text;
  `iter_tnef_attachments` returns `[("report.txt", "text/plain", b"…")]`.
- **api:** TNEF mbox → `/inner` lists the inner file with its `mime`/`size`; `/inner/0` downloads its
  bytes with `nosniff`; a non-TNEF attachment → `{files: []}`; bad `k` → 404.
- **Browser:** Files mode — open a `winmail.dat` → reader shows unwrapped text + a Contained-files
  list whose Download links fetch the inner bytes; a `.vcf`/vCard shows labeled contact text under
  Contacts; a `.doc` shows extracted text (best-effort).
- **Container e2e:** verify a real `winmail.dat`, a real `.doc` (antiword), and a real vCard from the
  actual mailbox.

## FTS note
TNEF/`.doc`/vCard **content search** reflects a re-index (indexer unchanged; on-demand viewing works
immediately). A re-index after deploy makes them searchable — offer it.

## Out of scope (YAGNI)
- Making TNEF inner files first-class Files-mode entries / individually searchable.
- A dedicated category for `winmail.dat`.
- Perfect `.doc` fidelity (formatting/tables) — text-only, best-effort.
- vCard photo rendering; nested TNEF-in-TNEF beyond one level.
