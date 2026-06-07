# In-Browser Viewers & Players (Tier 1 + Tier 2) — Design

**Date:** 2026-06-07
**Status:** Approved

## Goal

Add in-browser viewing/playing for the attachment types that currently download-only, based
on the mailbox's actual MIME distribution:

**Tier 1**
1. **Audio player** — HTML5 `<audio>` for `audio/*`.
2. **Video player** — HTML5 `<video>` for a vetted set of `video/*`.
3. **Calendar viewer** — parse `.ics` into readable, labeled text; fix the categorization so
   `application/ics` lands in **Calendar** (today it falls to **Other**).
4. **BMP preview** — allowlist `image/bmp` so it previews like other raster images.

**Tier 2**
5. **CSV table viewer** — render `text/csv` as an escaped HTML table in the reader.
6. **Text-like "Other" extraction** — extract a few text-bearing `application/*` types
   (`json`, `xml`, `yaml`) so they're readable in the text pane (and searchable on re-index).

All in the **Files-mode reader** (`openFile`), matching today's image-preview pattern.

## Security framing (drives every decision)

The reader's PDF iframe is **not** sandboxed, which is why `_SAFE_INLINE_MIMES` is deliberately
tiny. The dividing line:

- **`<audio>`, `<video>`, `<img>` do not create a scripting context** → serving those bytes inline
  is safe. We extend `_SAFE_INLINE_MIMES` with non-scriptable media + `image/bmp` only, always with
  `X-Content-Type-Options: nosniff`. We do **not** add `text/html` or `image/svg+xml`.
- **CSV table** is built in the (non-sandboxed) main document, so every cell is `escapeHtml`'d.
- **ICS / text-like** content renders via `textContent` (never `innerHTML`).

## Backend

### `api.py` — `_SAFE_INLINE_MIMES`
Add (non-scriptable, inline-safe):
```
image/bmp,
audio/mpeg, audio/mp4, audio/x-m4a, audio/aac, audio/ogg, audio/wav, audio/webm,
video/mp4, video/webm, video/ogg, video/quicktime
```
No route logic changes — the existing `attachment(..., inline=True)` path already gates on this
set and sends `nosniff`.

### `filetypes.py` — Calendar categorization
`category_for_mime` must map `text/calendar`, **`application/ics`**, and `text/x-vcalendar` to
**Calendar**. (Today only `text/calendar` matches; `application/ics` — the majority of real
invites — falls through to **Other**.)

### `extract.py` — ICS + text-like types
- **ICS** (`text/calendar`, `application/ics`, `text/x-vcalendar`): a small dependency-free parser.
  Unfold RFC 5545 folded lines (a line beginning with a space/tab continues the previous one), then
  for each `VEVENT` emit labeled lines: `Summary`, `When` (DTSTART→DTEND), `Location`, `Organizer`,
  `Attendees`, `Description`. Property values are unescaped (`\,`→`,`, `\n`→newline, `\;`→`;`).
  Returns `""` if no VEVENT is found (e.g. a VTODO-only file) — acceptable.
- **Text-like `application/*`**: `application/json`, `application/xml`, `application/x-yaml`,
  `application/yaml` decode as UTF-8 text (like the existing `text/*` branch). Dispatch these before
  the generic fallthrough.

All wrapped by the existing try/except → `""` on error.

## Frontend (`static/`)

### Reader panes
`#reader` gains three elements after `#reader-image`:
```html
<audio id="reader-audio" controls hidden></audio>
<video id="reader-video" controls hidden></video>
<div id="reader-table" hidden></div>
```
There are now **seven** mutually-exclusive panes: `body, pdf, text, image, audio, video, table`.

### `showOnlyPane(el)` helper (refactor)
Centralize exclusivity. Hide every pane except `el`; when hiding `#reader-audio`/`#reader-video`,
**stop playback** (`pause()`, clear `src`, `load()`); when hiding `#reader-pdf`/`#reader-image`,
clear `src`. `openMessage`, `viewPdf`, `openFile`, and `setMode` call `showOnlyPane(...)` instead of
their ad-hoc hide lists (body shown for messages; nothing/respective pane otherwise). `setMode`
hides all (passes a non-pane / null so every pane is hidden, then folders mode shows body as today).

### `openFile(mid, idx, filename, mime, size)` dispatch
Header (filename · mime · size) and the attachments area (**Download** + **Open email**) are set for
every type. Then branch on `mime` (lowercased):
- `audio/*` → `showOnlyPane(readerAudio)`; `readerAudio.src = inlineUrl`.
- `video/*` → `showOnlyPane(readerVideo)`; `readerVideo.src = inlineUrl`.
- `image/*` → `showOnlyPane(readerImage)`; `readerImage.src = inlineUrl`.
- `text/csv` (or filename ends `.csv`) → `showOnlyPane(readerTable)`; fetch `/text`, parse CSV,
  render an escaped `<table>` (cap at 500 rows, note truncation).
- else → `showOnlyPane(readerText)`; fetch `/text`; show text or "No extractable text…".

`inlineUrl = /api/messages/{mid}/attachments/{idx}?inline=1`. The Download link always remains, so
a codec the browser can't play still has a working fallback.

### CSV parsing
A small dependency-free parser handling quoted fields, escaped quotes (`""`), and commas/newlines
inside quotes. Render `<table>` with `<th>` for row 0 and `<td>` for the rest, **every cell
`escapeHtml`'d**. Cap at 500 data rows; if more, append a "(showing first 500 rows)" note.

### `style.css`
Styles for `#reader-audio` (full width, padding), `#reader-video` (max-width/height: 100%,
object-fit/centered like the image), and `#reader-table` (scroll, collapsed borders, small mono
font, sticky header row).

## Error handling
- Media the browser can't decode → the element shows its native error UI; the Download link works.
- ICS with no VEVENT, or a corrupt office/media file → `extract_text` returns `""` → "no text".
- CSV parse on a malformed file → best-effort rows; never throws (guard the parser).
- Unknown/text-like decode failure → `""`.

## Testing
- **filetypes:** `application/ics`, `text/calendar`, `text/x-vcalendar` → `Calendar`.
- **extract:** an ICS string with a VEVENT → text containing the summary/location/organizer; a
  folded-line value is correctly unfolded; `application/json` bytes → the JSON text; corrupt → `""`.
- **api:** `GET /api/messages/{id}/attachments/{idx}?inline=1` for an `audio/mpeg` and a `video/mp4`
  attachment returns `Content-Disposition: inline` + `nosniff` (allowlisted); a `text/html`
  attachment still forced to `attachment` (guard the allowlist didn't widen unsafely); `image/bmp`
  inline.
- **Browser:** Files mode — an audio file plays in an `<audio>` control; a video file shows a
  `<video>` player; a `.ics` shows labeled event text; a `.csv` renders as a table; switching files
  stops the previous audio/video; Open-email + Download present throughout; panes stay exclusive.

## FTS note
The full-text index reflects ICS/text-like content only after a **re-index** (the indexer is
unchanged; on-demand reader works immediately). Filename search and viewing work now. A re-index is
an optional follow-up (offer it after deploy).

## Out of scope (YAGNI)
- HTTP Range / streaming for large video (buffered inline is fine ≤25 MB; seeking limited).
- A bespoke calendar "card" UI (labeled text covers it; revisit later).
- Inline media/players inside the **folders-mode** message reader (Files-mode only for now).
- Rich DOCX/XLSX/PPTX layout rendering (text extraction already covers search + preview).
- SVG inline (XSS-sensitive; none present), ZIP listing, `pkpass`/`rpmsg` (one file each).
- Recategorizing `application/json|xml|yaml` out of **Other** (only adding extraction).
