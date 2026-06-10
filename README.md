# mbox Assistant

A local, Dockerized tool for a single Google Takeout `.mbox` file. It indexes the archive
once and serves a web UI to browse, search, and view messages and attachments, with two
optional tiers: **local semantic search** and a **Claude-powered assistant** for cited
conversations about the mailbox.

The mbox is mounted **read-only** and never modified. Nothing leaves the machine unless the
assistant tier is enabled, and then only the snippets retrieved for a given question are sent
to Anthropic.

The tool has three capability tiers, each a superset of the previous. You select how far to go
with environment variables:

| Tier | Enable | Adds | Network |
|---|---|---|---|
| **1. Viewer + full-text search** | default | Browse by label / attachment type, full-text search over text and attachments, inline viewers, export | None |
| **2. Semantic search** | `SEMANTIC_SEARCH=1` | Vector search "by meaning" across the archive | None (local model) |
| **3. AI assistant** | `ASSISTANT_ENABLED=1` + API key | Multi-turn cited chat about people, relationships, timelines, and files | Retrieved snippets sent to Anthropic |

## Capabilities

### Core — viewer + full-text search (always on)

- Browse by **Folders** (Gmail labels, from `X-Gmail-Labels`) or **Files** (attachment
  category).
- **Full-text search** over subjects, senders, bodies, **extracted attachment text** (PDF,
  DOCX, legacy DOC, PPTX/PPT, XLSX/XLS, ICS, vCard), and the file names inside `winmail.dat`
  and zip/tar archives. Snippets + highlighting; filters (date range, sender, has-attachment)
  and sort.
- **Inline viewers/players:** PDF, image preview + thumbnail gallery, HTML5 audio/video
  (autoplay on open; notice for browser-undecodable WMA/WMV), CSV/spreadsheet tables,
  calendar/contact cards, TNEF (`winmail.dat`) unwrapping, and archive content listings.
  Legacy `.doc`/`.ppt` text via bundled `antiword`/`catppt`.
- Sanitized HTML in a sandboxed iframe; tracking pixels / remote images blocked by default,
  with a per-message toggle and an opt-in durable offline image archive.
- **Export:** any message as `.eml` (original RFC-822 bytes); bulk-zip a category's or a
  search's attachments.
- Infinite scroll; keyboard shortcuts (`/` focus search, `j`/`k` or ↑/↓ next/prev, `Esc`
  blur); integrity report (indexed vs. skipped).

### Tier 2 — semantic search (`SEMANTIC_SEARCH=1`)

- Builds a **local vector index** alongside the keyword index (one-time background build; see
  [Build times](#build-times)).
- Adds a **Keyword / Semantic** toggle next to the search box; semantic mode matches by meaning
  (e.g. "flight booking" matches "airline reservation").
- The embedding model `BAAI/bge-small-en-v1.5` (~130 MB) runs on CPU inside the container. No
  API key; no data leaves the machine.

### Tier 3 — AI assistant (`ASSISTANT_ENABLED=1` + `ANTHROPIC_API_KEY`)

- An **Ask** tab with multi-turn chat over the whole archive; it becomes the default view when
  enabled.
- **Cited Markdown answers.** Every claim links to its source message(s) as `[#id]` chips,
  clickable while the answer is still streaming. Clicking a chip opens that email in a side
  pane; for a file-centric email (audio/video/PDF/document) the player/viewer opens and plays
  automatically, with a link back to the message.
- **Attachment-catalog tool.** The assistant answers file questions ("how many audio files do
  I have?", "list every video") with exact counts and lists that match the **Files** tab —
  including audio/video/image files that the text index does not cover.
- **Retrieval.** Vector search selects relevant snippets before each answer; only those
  snippets are sent to Anthropic. The full mailbox, raw mbox bytes, and embeddings stay local.
- Model selectable via `ASSISTANT_MODEL` (default `claude-sonnet-4-6`).
- The header and browser title reflect the active tier: *mbox viewer* → *mbox semantic search*
  → *mbox assistant*.
- Approximate cost: a typical question sends 5–20k input tokens (~1–5 cents at Sonnet pricing);
  long file listings that return many rows cost more.

## Security & intended use

Single-user, local-only. **No authentication and no HTTPS** — the UI exposes the entire
mailbox to anyone who can reach it.

- Published on `127.0.0.1` (localhost) only by default. Do not bind to `0.0.0.0`, port-forward,
  or deploy on a shared/public host.
- For remote access, use a private VPN (e.g. Tailscale) or a reverse proxy that adds HTTPS
  **and** authentication.
- With the assistant enabled, retrieved snippets are sent to Anthropic. `ANTHROPIC_API_KEY` is
  read from the environment at startup and is never logged, persisted, or included in the index.

## Requirements

- **Docker** Desktop (macOS/Windows) or Docker Engine + the `docker compose` plugin (Linux).
  Allocate Docker ≥ 2 GB RAM.
- A **Google Takeout** mailbox export: [takeout.google.com](https://takeout.google.com/) →
  deselect all → select **Mail** → create and download the export → unzip. The `.mbox` is under
  `Takeout/Mail/` (often `All mail Including Spam and Trash.mbox`).
- **Disk:**
  - mbox — its own size, mounted read-only (not copied).
  - FTS index — ≈ 2% of the mbox (a 13 GB mailbox produced a ~125 MB index).
  - Semantic tier — adds a chunk + vector store (a few hundred MB for a ~54k-message mailbox).
- **RAM:** ~60 MB idle; a few hundred MB peak during indexing/extraction.
- **CPU:** 1 core works; more cores shorten the one-time build.
- **Assistant tier only:** an Anthropic API key and outbound access to the Anthropic API.
- **Optional GPU:** a host running [Ollama](https://ollama.com) can run the embedding pass on a
  GPU (see [Build times](#build-times)).

Both AI tiers are off by default; their dependencies ship in the image but are imported lazily,
so they cost no memory when off, and toggling a tier needs no rebuild.

## Configure the tier

Set these before starting, in `.env` or as environment variables:

| Goal | Variables |
|---|---|
| Full-text search only | *(none — default)* |
| + Semantic search | `SEMANTIC_SEARCH=1` |
| + AI assistant | `ASSISTANT_ENABLED=1` and `ANTHROPIC_API_KEY=sk-ant-...` (implies semantic search) |

Optional tuning: `ASSISTANT_MODEL`, `EMBED_BACKEND` (`local` | `ollama`), `EMBED_MODEL`,
`OLLAMA_URL` — see the [Configuration reference](#configuration-reference).

## Install & run

### macOS / Linux

```bash
# Full-text search only:
./run.sh "/path/to/Takeout/Mail/All mail Including Spam and Trash.mbox"   # or the containing folder

# With semantic search:
SEMANTIC_SEARCH=1 ./run.sh "/path/to/your.mbox"

# With the assistant:
ASSISTANT_ENABLED=1 ANTHROPIC_API_KEY=sk-ant-... ./run.sh "/path/to/your.mbox"

# Change the host port (default 9000):
PORT=9500 ./run.sh "/path/to/your.mbox"
```

Open **http://localhost:9000** (or your port). Stop with `Ctrl+C` (or `docker compose down`).

### Windows / docker compose (works on all platforms)

```bash
cp .env.example .env
# Edit .env: set MBOX_FILE (use the full Windows path with the drive letter), and add
# SEMANTIC_SEARCH=1 and/or ASSISTANT_ENABLED=1 + ANTHROPIC_API_KEY=sk-ant-... as desired.
docker compose up --build
```

Open **http://localhost:9000**. Stop with `Ctrl+C` (or `docker compose down`).

The compose file bind-mounts `MBOX_FILE` → `/data/mail.mbox` (read-only) and stores the index
in the named volume `mbox-index` (mounted at `/index`). `PORT` (default `9000`) sets the host
port; the container always listens on `9000` internally.

## Build times

The first run builds the index once; it is reused on later starts. The page loads immediately
and shows progress while building. There are up to three passes:

1. **Indexing (FTS)** — always. Streams the mbox (never loads it whole) and stores metadata +
   the full-text index.
2. **Chunking** — semantic/assistant only. Re-reads messages, extracts attachment text, and
   splits into overlapping windows. CPU/IO bound.
3. **Embedding** — semantic/assistant only. Converts each chunk into a vector. CPU by default;
   GPU via Ollama.

Measured on a **MacBook Air M4 / 16 GB** (Docker), **54,183 messages / 108,082 chunks**:

| Pass | Backend | Rate | Time |
|---|---|---|---|
| Indexing (FTS) | CPU | — | a few minutes |
| Chunking | CPU | ~257 chunks/s | ~7 min |
| Embedding | CPU `bge-small` (default) | ~9 chunks/s | ~3.1 h |
| Embedding | GPU — Ollama `nomic-embed-text` | ~15 chunks/s | ~1.9 h |

Times scale with mailbox size and CPU/GPU speed. The UI is usable throughout; semantic search
and the assistant become ready when embedding finishes (progress is shown in the status bar).
Indexing and chunking are quick; embedding is the long pass.

After the build, queries embed only the query string (milliseconds). The index lives in the
`mbox-index` volume and is reused across restarts, so subsequent starts are fast.

### GPU acceleration (optional)

Set `EMBED_BACKEND=ollama` with a host running [Ollama](https://ollama.com) (`OLLAMA_URL`,
default `http://host.docker.internal:11434`) to run embedding on the GPU. Notes:

- The Ollama model differs from the CPU default (`nomic-embed-text` is 768-dim vs. `bge-small`
  384-dim).
- The same backend must be available at query time (every query is embedded with the configured
  backend), so Ollama must keep running.
- Changing the model or backend re-embeds the archive from scratch.

## Re-indexing

The index is keyed to the mbox file's size and modification time. Replacing the mbox with an
updated export triggers a re-index on the next start. To force a clean rebuild:

```bash
docker compose down
docker volume rm mbox-mbox-index   # the name may be prefixed by the project directory
```

## Durability & offline image archive

The index is disposable — it rebuilds from the mbox. The optional **remote-image archive** is
not (it can only be re-created over the network), so it lives in a separate host folder
(`ARCHIVE_HOST_DIR`, by default `mbox-viewer-archive/` next to the mbox) holding `archive.db` +
`assets/`. Click **Archive remote images** to download them (tracking pixels and SVG/XML are
skipped; set `HTTPS_PROXY` to route through a VPN). A complete offline copy is **the mbox file +
the archive folder**.

## Limitations

- **No authentication and no HTTPS** — local use only (see Security & intended use).
- **One mbox file at a time.** No Maildir, multi-file, or nested-folder discovery (run a second
  instance on another port for a second file).
- **Built for Google Takeout** mbox (mboxrd format, `X-Gmail-Labels` / `X-GM-THRID`). Other mbox
  dialects mostly work but are not a tested target.
- **Read-only viewer** — no reply/compose/delete, and no message flags/notes.
- **Best-effort extraction.** Old binary PowerPoint without a text layer, encrypted/DRM files,
  and `rar`/`7z` archive contents are not extracted (such files still download).
- **WMA/WMV do not play in-browser** (no browser codec) — a notice and download link are shown.
- **No conversation threading** (messages are listed flat).
- **Attachment downloads are buffered, not streamed** — fine for Gmail's 25 MB cap; very large
  attachments use more memory.
- **Assistant retrieval is top-N**, and the attachment-catalog tool returns up to 500 rows per
  query; for very large result sets the answer is a subset (the Files tab pages through all).

## Configuration reference

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `MBOX_FILE` | host (compose) | **required** | Absolute path to your `.mbox` file on the host |
| `PORT` | host (compose) | `9000` | Host port (published on `127.0.0.1` only) |
| `ARCHIVE_HOST_DIR` | host (compose) | `mbox-viewer-archive/` next to the mbox | Durable host folder for the offline image archive |
| `SEMANTIC_SEARCH` | host (compose) | *(off)* | `1` enables local vector/semantic search |
| `ASSISTANT_ENABLED` | host (compose) | *(off)* | `1` enables the AI assistant (implies `SEMANTIC_SEARCH`) |
| `ANTHROPIC_API_KEY` | host (compose) | — | Required when `ASSISTANT_ENABLED=1` |
| `ASSISTANT_MODEL` | container | `claude-sonnet-4-6` | Anthropic model used by the assistant |
| `EMBED_BACKEND` | container | `local` | `local` (in-container CPU) or `ollama` (host GPU) |
| `EMBED_MODEL` | container | `BAAI/bge-small-en-v1.5` | Embedding model (CPU backend) |
| `OLLAMA_URL` | container | `http://host.docker.internal:11434` | Ollama base URL (used when `EMBED_BACKEND=ollama`) |
| `MBOX_PATH` | container | `/data/mail.mbox` | Path to the mbox inside the container |
| `INDEX_PATH` | container | `/index/index.db` | Path to the SQLite index inside the container |
| `ARCHIVE_DIR` | container | `/archive` | Path to the image archive inside the container |
| `HTTPS_PROXY` | host/container | — | Optional proxy for the image-archive downloader |

## Development (run without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # run the test suite
PYTHONPATH=src MBOX_PATH=/path/to/your.mbox INDEX_PATH=./index.db \
  .venv/bin/python -m mboxviewer.main
```

Then open http://localhost:9000.
