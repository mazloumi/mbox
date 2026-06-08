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

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (with the `docker compose` plugin)
- A Google Takeout mbox export (a single `.mbox` file) somewhere on your machine

## Quick start (recommended)

A default mbox path and port are pre-configured (see "Configuration reference"
below), so you can just run:

```bash
./run.sh
```

To use a different file, point the launch script at your mbox file (or the folder
containing it):

```bash
./run.sh /absolute/path/to/your.mbox
```

You can also point at the **folder** that holds the mbox — if it contains exactly
one `.mbox` file, it's picked automatically:

```bash
./run.sh "/Users/you/Downloads/Takeout/Mail"
```

Or pass the path as an environment variable instead of an argument:

```bash
MBOX_FILE=/absolute/path/to/your.mbox ./run.sh
```

Change the host port (default `9000`) with the `PORT` variable:

```bash
PORT=9500 ./run.sh /absolute/path/to/your.mbox
```

Then open **http://localhost:9000** (or your chosen port).

> On the **first** run the app indexes the whole mbox before serving. For a large
> (10GB+) file this can take several minutes — watch the terminal logs for
> `Building index...` / `Index complete`. The index is stored in a Docker volume
> (`mbox-index`) and **reused on later runs**, so subsequent starts are fast.

To stop the viewer, press `Ctrl+C` in the terminal (or run `docker compose down`).

## Alternative: docker compose directly

If you prefer not to use the script:

1. Copy `.env.example` to `.env` and set `MBOX_FILE` to the **absolute path** of your
   `.mbox` file (and optionally `ARCHIVE_HOST_DIR`, the durable image-archive folder):

   ```bash
   cp .env.example .env
   # then edit .env: set MBOX_FILE=/absolute/path/to/your.mbox
   #                 (optionally) ARCHIVE_HOST_DIR=/absolute/path/to/archive
   ```

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
| `MBOX_FILE`  | host (compose)   | `/path/to/your-mail.mbox` | Absolute path to your `.mbox` file on the host |
| `PORT`       | host (compose)   | `9000`             | Host port to expose the viewer on                  |
| `ARCHIVE_HOST_DIR` | host (compose) | `mbox-viewer-archive/` next to the mbox (via `run.sh`) | Durable host folder for the offline image archive |
| `MBOX_PATH`  | container        | `/data/mail.mbox`  | Path to the mbox inside the container              |
| `INDEX_PATH` | container        | `/index/index.db`  | Path to the SQLite index inside the container      |
| `ARCHIVE_DIR`| container        | `/archive`         | Path to the image archive inside the container     |
| `HTTPS_PROXY`| host/container   | —                  | Optional proxy for the image-archive downloader (privacy) |
