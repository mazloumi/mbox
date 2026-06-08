# mbox Viewer

Browse, search, and read a Google Takeout `.mbox` file in your browser. It runs in
Docker and reads your mbox file (mounted **read-only**) from a folder on your host
machine — nothing is uploaded anywhere and the source file is never modified.

**Two ways to browse** — by **Folders** (your Gmail labels) or by **Files** (attachment
type), via tabs in the header.

**Read & search**
- **Gmail labels become folders** (from the `X-Gmail-Labels` header)
- **Full-text search** across subjects, senders, bodies, **extracted attachment text**
  (PDF, DOCX, legacy DOC, PPTX/PPT, XLSX/XLS, ICS, vCard) **and the file names inside
  `winmail.dat` and zip/tar archives**
- **Search snippets + highlighting** (see *why* a result matched) and **filters/sort**
  (date range, sender, has-attachment, newest/oldest)
- **Read messages** with sanitized HTML in a sandboxed iframe; **infinite scroll** (no
  "load more" clicks)
- **Tracking pixels / remote images blocked by default**, with a per-message "Load remote
  images" toggle and an opt-in **durable offline image archive**

**Files mode — view content in the browser**
- Attachments grouped into **categories** (Documents, Spreadsheets, Presentations, Images,
  Archives, **Enclosures** (winmail.dat), **Signatures**, Calendar, Contacts, Media, Other),
  with a **filename-extension fallback** so files sent as `application/octet-stream` still
  land in the right category
- **Inline viewers/players:** PDF, image preview **+ a thumbnail gallery** for Images, HTML5
  **audio/video** (with a clear notice for browser-undecodable WMA/WMV), **CSV & spreadsheet
  tables**, ICS **calendar** cards, **vCard** contacts, **TNEF/`winmail.dat` unwrapping**
  (lists & downloads the files inside), and **archive (zip/tar) content listings**
- **Legacy formats** extracted via bundled `antiword` (.doc) and `catppt` (.ppt)

**Export & trust**
- **Download any message as `.eml`** (original RFC-822 bytes — re-importable anywhere)
- **Bulk export** — zip all of a category's (or a search's) attachments
- **Integrity report** in the footer (messages indexed vs. skipped, with reasons)
- **Schema-version guard** — a code change that needs a re-index triggers it automatically
- **Keyboard shortcuts:** `/` focus search · `j`/`k` (or ↑/↓) next/prev · `Esc` blur search

## ⚠️ Security & intended use — read this first

This is a **single-user, local-only** tool. It has **no authentication and no HTTPS**, and
it shows the entire contents of your mailbox to anyone who can reach it.

- **Run it only on your own computer.** By default the viewer is published on **`127.0.0.1`
  (localhost) only**, so it is not reachable from your network — keep it that way.
- **Do not deploy it on a server, a VPS, or any shared/public network**, do not port-forward
  it, and do not change the bind address to `0.0.0.0`. There is no login — exposing the port
  means exposing all of your email.
- If you genuinely need remote access, reach it over a **private VPN** (e.g. Tailscale) or put
  it behind a reverse proxy that adds **HTTPS *and* authentication** (e.g. Caddy + basic auth).
  Never expose the raw port.

Your mbox is mounted **read-only** and is never modified; nothing is uploaded anywhere.

## Prerequisites

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (macOS or Windows),
  or Docker Engine + the `docker compose` plugin (Linux). Docker is the only thing you install
  — Python and all other dependencies run inside the container.
- A **Google Takeout mailbox export** (a single `.mbox` file): go to
  [takeout.google.com](https://takeout.google.com/) → deselect all, select **Mail** → create
  the export → download and unzip it. The `.mbox` is under `Takeout/Mail/` (often named
  `All mail Including Spam and Trash.mbox`).
- Free disk space for the search index (roughly 1–2× the mbox size), stored in a Docker volume.

## Run it — macOS / Linux

Point the launch script at your `.mbox` file (or the folder that contains exactly one):

```bash
./run.sh "/Users/you/Downloads/Takeout/Mail/All mail Including Spam and Trash.mbox"
# or pass the containing folder:
./run.sh "/Users/you/Downloads/Takeout/Mail"
```

Change the host port (default `9000`) with `PORT`:

```bash
PORT=9500 ./run.sh "/path/to/your.mbox"
```

Then open **http://localhost:9000** (or your chosen port). Stop with `Ctrl+C` (or
`docker compose down`).

## Run it — Windows

`run.sh` is bash-only, so on Windows use Docker Compose directly (this works on macOS/Linux
too):

1. In the repo folder, copy `.env.example` to `.env` and set `MBOX_FILE` to your mbox path
   (use the Windows path with the drive letter):

   ```powershell
   Copy-Item .env.example .env
   # then edit .env, e.g.:
   #   MBOX_FILE=C:\Users\you\Downloads\Takeout\Mail\All mail Including Spam and Trash.mbox
   ```

2. Build and start:

   ```powershell
   docker compose up --build
   ```

3. Open **http://localhost:9000**. Stop with `Ctrl+C` (or `docker compose down`).

> **The first run indexes the whole mbox.** For a large (10 GB+) file this takes several
> minutes — the page loads immediately and shows progress, filling in as it indexes. The index
> lives in a Docker volume (`mbox-index`) and is **reused on later runs**, so subsequent starts
> are fast.

## Alternative: docker compose directly (macOS / Linux)

You can use the same `.env` + compose flow shown for Windows above:

1. `cp .env.example .env`, then edit `.env` to set `MBOX_FILE` (and optionally
   `ARCHIVE_HOST_DIR`, the durable image-archive folder).

2. Build and start:

   ```bash
   docker compose up --build
   ```

3. Open http://localhost:9000

How the mount works: the compose file bind-mounts your `MBOX_FILE` to
`/data/mail.mbox` **read-only** inside the container, and stores the search index in
the named volume `mbox-index` mounted at `/index`. `PORT` (default `9000`) sets the
host port; the container always listens on `9000` internally.

## Using the viewer

- **Folders / Files tabs (header):** switch the left pane between Gmail labels and
  attachment categories. Clicking the active tab collapses/expands the left pane.
- **Folders mode — left pane:** your Gmail labels with message counts; click one to list
  its messages (a message with multiple labels appears under each).
- **Files mode — left pane:** attachment categories with counts; click one to list its
  files in the middle pane. Click a file to view it in the reader (PDF/image inline,
  audio/video player, CSV/spreadsheet table, calendar/contact card, archive/winmail.dat
  contents, or extracted text). **Images** show as a thumbnail **gallery**. A **Download
  all** button zips the current category's (or search's) attachments.
- **Message list (middle pane):** **scrolls infinitely** — the next page loads
  automatically as you reach the bottom. Search results show a highlighted snippet.
- **Search box + filters:** full-text search over subjects, senders, bodies, and
  attachment/inner-file text. Use the filter row to narrow by **date range**, **sender**,
  **has-attachment**, and **sort** order. Search is scoped to a selected folder.
- **Reader (right pane):** the message, its attachments (click to download), a **Download
  .eml** link, and the sanitized body. Remote images are blocked by default; click **"Load
  remote images"** to load them for that message.
- **Keyboard:** `/` focuses search, `j`/`k` (or ↑/↓) move between messages, `Esc` leaves
  the search box.
- **Footer:** the mbox name, index state, image-archive stats, and an **integrity** line
  (`N indexed · M skipped`, with the skip reasons on hover).

## Re-indexing

The index is keyed to the source file's size and modification time. If you replace
the mbox with an updated export, the app detects the change on next start and
re-indexes automatically. To force a clean rebuild, remove the index volume:

```bash
docker compose down
docker volume rm mbox-mbox-index   # volume name may be prefixed by the project dir
```

## Durability & offline archive

The search index is disposable — it rebuilds from the mbox. The **remote image
archive** is not (it can only be re-created over the network), so it lives in a
separate **host folder** (`ARCHIVE_HOST_DIR`, by default a `mbox-viewer-archive/`
folder next to your mbox) holding `archive.db` + `assets/`. Click **"Archive remote
images"** in the viewer to download them (tracking pixels and SVG/XML images are skipped;
set `HTTPS_PROXY` to route through a VPN).

Your complete offline copy is **the mbox file + the archive folder**. Back up those two
and you can delete the originals in Gmail, drop/rebuild the index, or move machines —
everything still renders offline. Re-running "Archive remote images" on an unchanged mbox
is an instant no-op (it records the mbox size/mtime and skips re-downloading).

## Limitations

- **No authentication and no HTTPS** — local-use only (see the security note above).
- **One mbox file at a time.** No Maildir, no multi-file or nested-folder discovery, no
  combining multiple exports. Point it at a single `.mbox` (run a second instance on another
  port for a second file).
- **Built for Google Takeout** mbox (mboxrd format, `X-Gmail-Labels` / `X-GM-THRID` headers).
  Other mbox dialects mostly work but aren't a tested target.
- **Read-only viewer** — no reply/compose/delete, and no message flags/stars/notes yet.
- **Best-effort extraction.** Legacy `.doc`/`.ppt` use bundled `antiword`/`catppt`; old binary
  PowerPoint without a text layer, encrypted/DRM files, and `rar`/`7z` archive *contents* aren't
  extracted. Such files still download.
- **WMA/WMV can't play in-browser** (no browser codec) — a notice + download link is shown.
- **Conversation threading isn't implemented yet** (messages are listed flat).
- **Attachment downloads are buffered, not streamed** — fine for Gmail's 25 MB cap; very large
  attachments would use more memory.
- The first index of a very large (10 GB+) mbox can take several minutes.

## Roadmap

Ideas not yet built (rough priority order):

- **Conversation threading** — group replies into threads using Gmail's `X-GM-THRID`
- **Local state** — stars, notes, and saved searches (stored alongside the durable archive,
  not in the disposable index)
- **Advanced query syntax** — `from:`, `has:attachment`, date operators in the search box
- **Rendered office/slide previews** — pixel-faithful pages via headless LibreOffice
  (heavier dependency; today slides/sheets are text/table views)
- **More archive formats** — `.rar` / `.7z` content listing
- **Authentication + HTTPS** — for exposing the viewer beyond `localhost` (e.g. behind a
  reverse proxy); not needed for the default local-only use
- **Analytics** — top senders / volume-over-time over the whole archive

## Development (run without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # run the test suite
PYTHONPATH=src MBOX_PATH=/path/to/your.mbox INDEX_PATH=./index.db \
  .venv/bin/python -m mboxviewer.main
```

Then open http://localhost:9000.

## Configuration reference

| Variable     | Where            | Default            | Meaning                                            |
|--------------|------------------|--------------------|----------------------------------------------------|
| `MBOX_FILE`  | host (compose)   | **required**       | Absolute path to your `.mbox` file on the host (no default) |
| `PORT`       | host (compose)   | `9000`             | Host port (published on `127.0.0.1` only)          |
| `ARCHIVE_HOST_DIR` | host (compose) | `mbox-viewer-archive/` next to the mbox (via `run.sh`) | Durable host folder for the offline image archive |
| `MBOX_PATH`  | container        | `/data/mail.mbox`  | Path to the mbox inside the container              |
| `INDEX_PATH` | container        | `/index/index.db`  | Path to the SQLite index inside the container      |
| `ARCHIVE_DIR`| container        | `/archive`         | Path to the image archive inside the container     |
| `HTTPS_PROXY`| host/container   | —                  | Optional proxy for the image-archive downloader (privacy) |
