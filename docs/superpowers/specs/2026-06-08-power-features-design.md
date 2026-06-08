# Power Features (Tier 1 + Gallery + Shrink-Other + Bulk Export + Shortcuts) — Design

**Date:** 2026-06-08
**Status:** Approved

## Goal

A batch of high-leverage features, treating the app as an **archive of record**:

1. **Tier 1**
   - **`.eml` export** — download any message as its original RFC-822 bytes.
   - **Integrity report** — surface how many messages were indexed vs skipped (with reasons),
     so the archive can be trusted before deleting from Gmail.
   - **Search snippets + highlighting** — show *why* a result matched (subject + body preview with
     the query terms highlighted).
   - **Search filters + sort** — date range, sender contains, has-attachment, and sort order.
2. **Image gallery** — the Images category renders as a thumbnail grid.
3. **Shrink "Other"** — give `winmail.dat` an **Enclosures** category and S/MIME/PGP signatures a
   **Signatures** category.
4. **Bulk export** — "Download all" zips a category's (or search's) attachments (with safety caps).
5. **Keyboard shortcuts** — `/` focus search, `j`/`k` next/prev, `Esc` blur.

Several features need a re-index (a new `preview` column + new categories) → one `SCHEMA_VERSION`
bump (2→ next); the schema-version guard re-indexes automatically on deploy.

## Decisions

- **`.eml` = original bytes.** Serve the message's raw RFC-822 bytes (mbox `From ` line stripped,
  mboxrd `>From` unescaped) — re-importable into any mail client. Not the parsed/sanitized form.
- **Integrity is tracked at index time** (the indexer already `try/except`s each message). Count
  skips + keep a small sample of `(offset, reason)`; store counts in `meta`; expose via
  `/api/integrity` and a footer line.
- **Snippets come from a stored `preview`** (first ~300 chars of the body plaintext), not from
  re-extracting per result (too slow) and not from FTS `snippet()` (the index is contentless).
  Highlighting happens in the **main document** result rows (escape, then wrap matches in `<mark>`) —
  never inside the sanitized body iframe.
- **Filters/sort are server-side** on the existing `messages`/FTS queries. Sort default stays
  `date DESC`.
- **Image gallery is a render mode** of the existing list for the Images category; thumbnails use the
  existing allowlisted `?inline=1` endpoint with `loading="lazy"`. Clicking opens the reader (existing
  `openFile`). Infinite scroll still applies.
- **Bulk export streams a zip** built in a `SpooledTemporaryFile` (spills to disk, bounded memory),
  capped at **1000 files and 1 GB** (whichever first); entries are de-collided by prefixing the
  message id. Reuses `read_message`/`iter_attachments` (contract #1) and the same per-attachment read
  the download route uses. Caps are reported in the response/UI.
- **Shortcuts** ignore typing in inputs (except `Esc`), reuse the arrow-nav logic for `j`/`k`.

## Backend

### `reader.py`
- `read_message_bytes(path, offset, length) -> bytes`: read the span, drop the leading `From …` line,
  and un-escape mboxrd `>From `→`From ` (one `>`), returning the original RFC-822 message bytes.

### `filetypes.py` (shrink Other)
- `CATEGORY_ORDER`: insert `"Enclosures"` and `"Signatures"` (after `Archives`, before `Calendar`).
- `category_for_mime`: `application/ms-tnef`/`application/ms-tnefx` → **Enclosures**;
  `application/pkcs7-signature`, `application/x-pkcs7-signature`, `application/pgp-signature`,
  `application/pkcs7-mime` → **Signatures**.
- `_EXT_CATEGORY`: `.dat`→Enclosures; `.p7s .p7m .asc .sig`→Signatures.

### `store.py`
- **Schema:** add `preview TEXT` to `messages`; `create_schema` ALTER-adds it (guarded). Bump
  `SCHEMA_VERSION`.
- `add_message(..., preview)` stores the preview.
- `list_messages` / `search` gain optional `date_from`, `date_to`, `from_q`, `has_attachment`,
  `sort` ("date_desc"|"date_asc"). Build dynamic `WHERE`/`ORDER BY` with **bound params**; `search`
  keeps FTS `MATCH` + filters; default sort `date DESC` (search default `ORDER BY rank`). Selecting
  `*` already returns `preview`.
- `list_files_for_export(category, query, limit)` → like `list_files_by_category` but returns
  `(message_id, idx, filename, mime, size)` up to `limit` (no offset), for the zip builder.
- `integrity()` → `{indexed, skipped, sample}` read from `meta` (set by the indexer).

### `indexer.py`
- Compute `preview` = first 300 chars of `_body_text(msg)` (collapsed whitespace); pass to
  `add_message`.
- Track `skipped` count + a capped sample of `(offset, short-reason)`; after the loop,
  `set_meta("indexed_count", …)`, `set_meta("skipped_count", …)`,
  `set_meta("skipped_sample", json)` (≤25 entries).

### `api.py`
- `GET /api/messages/{id}/raw` → `read_message_bytes` as `message/rfc822`,
  `Content-Disposition: attachment; filename="message-{id}.eml"` (404/503 like the attachment route).
- `GET /api/integrity` → `store.integrity()` + `message_count`.
- `GET /api/files/export?category=&q=` → stream a zip of the matching attachments (cap 1000 files /
  1 GB; `SpooledTemporaryFile`; entry names `f"{message_id}-{filename}"`, sanitized);
  `Content-Disposition: attachment; filename="mbox-{category-or-search}.zip"`. 404 if nothing matches.
- `/api/messages` & `/api/search` gain `date_from`, `date_to`, `from_q`, `has_attachment` (bool),
  `sort` query params (passed through to the store) and include `preview` in `_msg_summary`.

## Frontend (`static/`)
- **Result rows:** show subject + a one-line **preview**, with the active query terms wrapped in
  `<mark>` (escape first). A small "Open email" path is unchanged.
- **`.eml` link:** the message reader header gets a **Download .eml** link (`/api/messages/{id}/raw`).
- **Search filters bar:** a compact, collapsible row under the search box (date from/to, sender
  contains, ☑ has attachment, sort select); changing any re-runs the search/list. Hidden in Files mode
  (as the search box is — but search is now shown in both; filters apply to message search).
- **Image gallery:** in Files mode with category Images, render `#message-list` as a CSS-grid of
  `loading="lazy"` thumbnails (the `?inline=1` URL); clicking opens the reader. Other categories
  unchanged. Infinite scroll preserved.
- **Bulk export:** a **Download all** button in the Files toolbar (when a category or query is active)
  → `GET /api/files/export?...` (browser download). Shows the cap note if hit.
- **Integrity:** a small footer line "Index: N indexed · M skipped" (from `/api/integrity`), the M
  with a tooltip/expandable list of sample reasons.
- **Shortcuts:** extend the keydown handler — `/` focuses search; `j`/`k` = down/up (reuse arrow nav);
  `Esc` blurs the search box. Ignore when typing in an input (except `Esc`).

## Security
- `.eml`/bulk-zip reuse the existing offset read + `iter_attachments` (contract #1); zip entry names
  are sanitized (basename, strip path separators) to prevent zip-path issues; caps bound resource use.
- Snippet/preview and highlighted terms render in the **main document** via `escapeHtml` then a
  `<mark>` wrap of escaped matches — never raw email HTML, never the iframe.
- Image gallery uses only the allowlisted inline endpoint (raster types) + `nosniff`.
- Filter/sort values are **bound SQL params**; `sort` is mapped through a fixed allowlist (never
  interpolated).

## Testing
- **reader:** `read_message_bytes` round-trips a known message (From-line stripped, `>From` unescaped).
- **filetypes:** ms-tnef→Enclosures, p7s/pkcs7→Signatures (+ in CATEGORY_ORDER).
- **store:** preview stored + returned; `list_messages`/`search` honor date/from/has-attachment/sort;
  `list_files_for_export` caps; `integrity()` reads meta.
- **indexer:** preview populated; skipped count/sample recorded for a deliberately-broken message.
- **api:** `/raw` → message/rfc822 + filename; `/api/integrity` shape; `/api/files/export` returns a
  valid zip whose entries are the category's attachments; filtered/sorted `/api/search`.
- **Browser:** result snippet highlights terms; a date/sender filter narrows results; sort flips order;
  `.eml` downloads; Images shows a thumbnail grid; "Download all" downloads a zip; `/` focuses search,
  `j`/`k` navigate, `Esc` blurs; footer shows the integrity line.
- **Container e2e:** Enclosures/Signatures categories appear (Other shrinks); a real `.eml` opens in a
  mail client; bulk export of a small category works.

## Out of scope (YAGNI)
- Conversation threading; local stars/notes/saved searches (separate future work).
- Highlighting inside the rendered HTML body (iframe) — result-row highlight only.
- rar/7z bulk handling beyond what already lists; transcoding; auth/HTTPS.
- Server-generated image thumbnails (use the browser's lazy-loaded full image, scaled by CSS).

## Roadmap (README "future" section)
Conversation threading (via `X-GM-THRID`); local state (stars/notes/saved searches); advanced query
syntax; rendered office/slide previews (LibreOffice); rar/7z extraction; auth + HTTPS for non-local
use; per-sender/volume analytics.
