import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _msg_summary(row):
    return {
        "id": row["id"], "subject": row["subject"], "from": row["from_addr"],
        "to": row["to_addr"], "date": row["date"],
    }


def create_app(settings):
    app = FastAPI(title="mbox viewer")
    store = Store(settings.index_path)
    store.create_schema()
    if not index_is_current(settings, store):
        print("Building index...")
        n = build_index(settings, store, progress=lambda c: print(f"  indexed {c}"))
        print(f"Index complete: {n} messages")
    app.state.store = store
    app.state.settings = settings

    @app.get("/api/labels")
    def labels():
        return [{"name": n, "count": c} for n, c in store.list_labels()]

    @app.get("/api/messages")
    def messages(label: Optional[str] = None, page: int = 1, page_size: int = 50):
        offset = (page - 1) * page_size
        rows = store.list_messages(label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/search")
    def search(q: str = Query(...), label: Optional[str] = None, page: int = 1, page_size: int = 50):
        offset = (page - 1) * page_size
        rows = store.search(q, label, page_size, offset)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/messages/{message_id}")
    def message_detail(message_id: int, allow_remote: bool = False):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        msg = read_message(settings.mbox_path, row["offset"], row["length"])
        mime, content = get_display_body(msg)
        body_html = sanitize_html(content if mime == "text/html" else
                                  "<pre>" + content + "</pre>", allow_remote=allow_remote)
        atts = [{"idx": a["idx"], "filename": a["filename"], "mime": a["mime"], "size": a["size"]}
                for a in store.get_attachments(message_id)]
        return {**_msg_summary(row), "body_html": body_html, "attachments": atts}

    @app.get("/api/messages/{message_id}/attachments/{idx}")
    def attachment(message_id: int, idx: int):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        msg = read_message(settings.mbox_path, row["offset"], row["length"])
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return Response(
                    content=payload, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        raise HTTPException(404, "attachment not found")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
