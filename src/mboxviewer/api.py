import html
import os
import re
import threading
import urllib.parse
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current
from .status import IndexStatus

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _msg_summary(row):
    return {
        "id": row["id"], "subject": row["subject"], "from": row["from_addr"],
        "to": row["to_addr"], "date": row["date"],
    }


def _render_body(mime, content, allow_remote=False):
    """HTML parts are sanitized; plain text is escaped and wrapped in <pre>."""
    if mime == "text/html":
        return sanitize_html(content, allow_remote=allow_remote)
    return "<pre>" + html.escape(content) + "</pre>"


def _content_disposition(filename, inline=False):
    """Safe Content-Disposition value; RFC 5987 for non-ASCII names."""
    filename = _CONTROL_CHARS.sub("", filename or "") or "attachment"
    kind = "inline" if inline else "attachment"
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        encoded = urllib.parse.quote(filename.encode("utf-8"))
        return f"{kind}; filename*=UTF-8''{encoded}"
    safe = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'{kind}; filename="{safe}"'


def create_app(settings, index_in_background=True):
    app = FastAPI(title="mbox viewer")
    store = Store(settings.index_path)
    store.create_schema()
    status = IndexStatus()
    app.state.store = store
    app.state.settings = settings
    app.state.status = status

    def _run_index():
        try:
            bytes_total = os.path.getsize(settings.mbox_path)
            status.start(bytes_total)
            n = build_index(settings, store, progress=status.update)
            status.update(n, bytes_total)
            status.finish()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            status.fail(exc)

    if index_is_current(settings, store):
        status.mark_ready(store.message_count())
    elif index_in_background:
        threading.Thread(target=_run_index, daemon=True).start()
    else:
        _run_index()

    @app.get("/api/status")
    def get_status():
        return status.snapshot()

    @app.get("/api/labels")
    def labels():
        return [{"name": n, "count": c} for n, c in store.list_labels()]

    @app.get("/api/messages")
    def messages(label: Optional[str] = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        offset = (page - 1) * page_size
        rows = store.list_messages(label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/search")
    def search(q: str = Query(...), label: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        offset = (page - 1) * page_size
        rows = store.search(q, label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/messages/{message_id}")
    def message_detail(message_id: int, allow_remote: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        mime, content = get_display_body(msg)
        body_html = _render_body(mime, content, allow_remote=allow_remote)
        atts = [{"idx": a["idx"], "filename": a["filename"], "mime": a["mime"], "size": a["size"]}
                for a in store.get_attachments(message_id)]
        return {**_msg_summary(row), "body_html": body_html, "attachments": atts}

    @app.get("/api/messages/{message_id}/attachments/{idx}")
    def attachment(message_id: int, idx: int, inline: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return Response(
                    content=payload, media_type=mime,
                    headers={"Content-Disposition": _content_disposition(filename, inline=inline)})
        raise HTTPException(404, "attachment not found")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
