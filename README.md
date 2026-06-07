# mbox Viewer

Browse, search, and read a Google Takeout `.mbox` file in your browser. It runs in
Docker and reads your mbox file (mounted **read-only**) from a folder on your host
machine — nothing is uploaded anywhere and the source file is never modified.

- **Gmail labels become folders** (from the `X-Gmail-Labels` header)
- **Full-text search** across message subjects, senders, bodies **and** extracted
  attachment text (PDF / DOCX)
- **Read messages** with sanitized HTML rendered in a sandboxed iframe
- **View and download attachments**
- **Tracking pixels / remote images are blocked by default**, with a per-message
  "Load remote images" toggle

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
   `.mbox` file:

   ```bash
   cp .env.example .env
   # then edit .env and set MBOX_FILE=/absolute/path/to/your.mbox
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

- **Folders (left pane):** your Gmail labels, each with its message count. Click one
  to list its messages. A message with multiple labels appears under each.
- **Message list (middle pane):** click a message to open it. If a label has more
  than 50 messages, a **"Load more…"** row appears at the bottom to page through them.
- **Search box:** type to full-text search subjects, senders, bodies, and attachment
  text. If a folder is selected, search is scoped to that folder.
- **Reader (right pane):** shows the message, its attachments (click to download),
  and the body. Remote images are blocked by default; click **"Load remote images"**
  to load them for that message.

## Re-indexing

The index is keyed to the source file's size and modification time. If you replace
the mbox with an updated export, the app detects the change on next start and
re-indexes automatically. To force a clean rebuild, remove the index volume:

```bash
docker compose down
docker volume rm mbox-mbox-index   # volume name may be prefixed by the project dir
```

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
| `MBOX_PATH`  | container        | `/data/mail.mbox`  | Path to the mbox inside the container              |
| `INDEX_PATH` | container        | `/index/index.db`  | Path to the SQLite index inside the container      |
