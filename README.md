# mbox Viewer

Browse, search, and read a Google Takeout `.mbox` file in your browser. Runs in
Docker and reads the mbox file (mounted read-only) from your host machine. Gmail
labels become folders; full-text search covers message bodies and attachment text
(PDF/DOCX).

## Quick start

1. Copy `.env.example` to `.env` and set `MBOX_FILE` to the absolute path of your
   `.mbox` file on the host.
2. Build and run:

   ```bash
   docker compose up --build
   ```

3. On first run the app indexes the mbox (this can take several minutes for large
   files; watch the logs). The index is stored in a Docker volume and reused on
   later starts.
4. Open http://localhost:8000

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
PYTHONPATH=src MBOX_PATH=/path/to.mbox INDEX_PATH=./index.db .venv/bin/python -m mboxviewer.main
```
