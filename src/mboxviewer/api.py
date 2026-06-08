import html
import os
import re
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, read_message_bytes, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current
from .status import IndexStatus
from . import assets
from . import filetypes
from .assetstore import AssetStore
from .archive import ArchiveStatus, run_archive
from .extract import extract_text, iter_tnef_attachments

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Only these MIME types may be served with an inline Content-Disposition. Anything
# else (e.g. text/html, image/svg+xml) is forced to attachment to prevent the
# browser from rendering attacker-controlled content same-origin (XSS).
_SAFE_INLINE_MIMES = frozenset({
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    # Non-scriptable media — safe to serve inline for <audio>/<video> playback.
    "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/aac",
    "audio/ogg", "audio/wav", "audio/webm",
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
})


def _msg_summary(row):
    return {
        "id": row["id"], "subject": row["subject"], "from": row["from_addr"],
        "to": row["to_addr"], "date": row["date"], "preview": row["preview"],
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
    # create_schema() must run here, synchronously, BEFORE the indexer thread is
    # spawned below: index_is_current() and the first request handlers query tables
    # that must already exist, and the background thread's clear()/writes must never
    # race schema creation. Do not move this into _run_index().
    store.create_schema()
    status = IndexStatus()
    app.state.store = store
    app.state.settings = settings
    app.state.status = status

    asset_store = AssetStore(settings.archive_dir)
    asset_store.create_schema()
    archive_status = ArchiveStatus()
    archive_lock = threading.Lock()
    app.state.asset_store = asset_store
    app.state.archive_status = archive_status

    def _run_index():
        try:
            bytes_total = os.path.getsize(settings.mbox_path)
            status.start(bytes_total)
            n = build_index(settings, store, progress=status.update)
            status.update(n, bytes_total)
            status.finish()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            sys.stderr.write(f"Indexing failed: {exc}\n")
            status.fail(exc)
        except BaseException as exc:  # interrupted (SystemExit/etc.): don't get stuck
            status.fail(RuntimeError(f"indexer interrupted: {exc}"))
            raise

    if index_is_current(settings, store):
        status.mark_ready(store.message_count())
    elif index_in_background:
        threading.Thread(target=_run_index, daemon=True).start()
    else:
        _run_index()

    @app.get("/api/status")
    def get_status():
        snap = status.snapshot()
        snap["mbox"] = settings.mbox_name or os.path.basename(settings.mbox_path)
        snap["current"] = index_is_current(settings, store)
        return snap

    @app.get("/api/labels")
    def labels():
        return [{"name": n, "count": c} for n, c in store.list_labels()]

    @app.get("/api/messages")
    def messages(label: Optional[str] = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 date_from: Optional[str] = None, date_to: Optional[str] = None,
                 from_q: Optional[str] = None, has_attachment: bool = False,
                 sort: str = "date_desc"):
        offset = (page - 1) * page_size
        rows = store.list_messages(label, page_size, offset,
                                   date_from=date_from, date_to=date_to, from_q=from_q,
                                   has_attachment=has_attachment, sort=sort)
        return {"messages": [_msg_summary(r) for r in rows], "page": page}

    @app.get("/api/search")
    def search(q: str = Query(...), label: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               from_q: Optional[str] = None, has_attachment: bool = False,
               sort: str = "date_desc"):
        offset = (page - 1) * page_size
        rows = store.search(q, label, page_size, offset,
                            date_from=date_from, date_to=date_to, from_q=from_q,
                            has_attachment=has_attachment, sort=sort)
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
        if mime == "text/html":
            refs = assets.extract_image_refs(content)
            if refs:
                cached = asset_store.cached_asset_hashes({assets.url_hash(u) for (u, _, _) in refs})
                if cached:
                    content = assets.rewrite_cached_images(content, cached)
        body_html = _render_body(mime, content, allow_remote=allow_remote)
        atts = [{"idx": a["idx"], "filename": a["filename"], "mime": a["mime"], "size": a["size"]}
                for a in store.get_attachments(message_id)]
        return {**_msg_summary(row), "body_html": body_html, "attachments": atts}

    @app.get("/api/messages/{message_id}/raw")
    def message_raw(message_id: int):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            raw = read_message_bytes(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        return Response(
            content=raw, media_type="message/rfc822",
            headers={
                "Content-Disposition": _content_disposition(f"message-{message_id}.eml"),
                "X-Content-Type-Options": "nosniff",
            })

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
                safe_inline = inline and mime in _SAFE_INLINE_MIMES
                return Response(
                    content=payload, media_type=mime,
                    headers={
                        "Content-Disposition": _content_disposition(filename, inline=safe_inline),
                        "X-Content-Type-Options": "nosniff",
                    })
        raise HTTPException(404, "attachment not found")

    def _tnef_inner(message_id, idx):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                if mime != "application/ms-tnef":
                    return []
                try:
                    return iter_tnef_attachments(payload)
                except Exception:
                    return []
        raise HTTPException(404, "attachment not found")

    @app.get("/api/messages/{message_id}/attachments/{idx}/inner")
    def tnef_inner_list(message_id: int, idx: int):
        inner = _tnef_inner(message_id, idx)
        return {"files": [{"k": k, "name": name, "mime": mime, "size": len(blob)}
                          for k, (name, mime, blob) in enumerate(inner)]}

    @app.get("/api/messages/{message_id}/attachments/{idx}/inner/{k}")
    def tnef_inner_file(message_id: int, idx: int, k: int, inline: bool = False):
        inner = _tnef_inner(message_id, idx)
        if k < 0 or k >= len(inner):
            raise HTTPException(404, "inner attachment not found")
        name, mime, blob = inner[k]
        safe_inline = inline and mime in _SAFE_INLINE_MIMES
        return Response(
            content=blob, media_type=mime,
            headers={
                "Content-Disposition": _content_disposition(name, inline=safe_inline),
                "X-Content-Type-Options": "nosniff",
            })

    @app.post("/api/archive/start")
    def archive_start():
        with archive_lock:
            if archive_status.running():
                return {"started": False}
            archive_status.mark_running()
            threading.Thread(target=run_archive,
                             args=(settings, store, asset_store, archive_status),
                             daemon=True).start()
            return {"started": True}

    @app.get("/api/archive/status")
    def archive_status_route():
        snap = archive_status.snapshot()
        counts = asset_store.asset_counts()
        meta = asset_store.get_archive_meta()
        try:
            cur_size = os.path.getsize(settings.mbox_path)
            cur_mtime = int(os.path.getmtime(settings.mbox_path))
        except OSError:
            cur_size = cur_mtime = None
        snap["archived"] = counts
        snap["up_to_date"] = bool(
            meta and counts["failed"] == 0
            and meta["source_size"] == cur_size and meta["source_mtime"] == cur_mtime)
        return snap

    @app.get("/api/asset/{asset_hash}")
    def get_asset(asset_hash: str):
        if not re.fullmatch(r"[0-9a-f]{64}", asset_hash):
            raise HTTPException(404, "not found")
        row = asset_store.get_asset(asset_hash)
        if row is None or row["status"] != "ok":
            raise HTTPException(404, "not cached")
        data = assets.read_asset_bytes(settings.archive_dir, asset_hash)
        if data is None:
            raise HTTPException(404, "asset missing")
        return Response(
            content=data, media_type=row["content_type"] or "application/octet-stream",
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/filetypes")
    def filetypes_route():
        counts = {r["category"]: r["c"] for r in store.attachment_category_counts()}
        return [{"category": cat, "count": counts[cat]}
                for cat in filetypes.CATEGORY_ORDER if counts.get(cat)]

    @app.get("/api/files")
    def files(category: Optional[str] = None, q: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        query = (q or "").strip()
        if not category and not query:
            return {"files": [], "page": page}
        offset = (page - 1) * page_size
        rows = store.list_files_by_category(category or None, page_size, offset, query=query or None)
        return {"files": [{"message_id": r["message_id"], "idx": r["idx"],
                           "filename": r["filename"], "size": r["size"], "mime": r["mime"],
                           "subject": r["subject"], "date": r["date"]} for r in rows],
                "page": page}

    @app.get("/api/integrity")
    def integrity():
        return {**store.integrity(), "messages": store.message_count()}

    @app.get("/api/files/export")
    def files_export(category: Optional[str] = None, q: Optional[str] = None):
        rows = store.list_files_for_export(
            category or None, (q or "").strip() or None, 1000)
        if not rows:
            raise HTTPException(404, "no files")
        CAP_BYTES = 1024 ** 3
        total = 0
        # Cache message_id -> parsed EmailMessage so each message is read/parsed once
        # even when it contributes several attachments to the export.
        msg_cache = {}
        spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        try:
            with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in rows:
                    if total >= CAP_BYTES:
                        break
                    mid = r["message_id"]
                    msg = msg_cache.get(mid)
                    if msg is None:
                        mrow = store.get_message_row(mid)
                        if mrow is None:
                            continue
                        msg = read_message(settings.mbox_path, mrow["offset"], mrow["length"])
                        msg_cache[mid] = msg
                    for a_idx, filename, mime, payload in iter_attachments(msg):
                        if a_idx == r["idx"]:
                            # Cap is enforced before writing so the loop actually stops.
                            if total + len(payload) > CAP_BYTES:
                                break
                            base = os.path.basename(filename or "file")
                            name = f"{mid}-{base}"
                            zf.writestr(name, payload)
                            total += len(payload)
                            break
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        spool.seek(0)
        data = spool.read()
        label = category or "search"
        return Response(
            content=data, media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(f"mbox-{label}.zip")})

    @app.get("/api/files/{message_id}/{idx}/text")
    def file_text(message_id: int, idx: int):
        row = store.get_message_row(message_id)
        if row is None:
            raise HTTPException(404, "message not found")
        try:
            msg = read_message(settings.mbox_path, row["offset"], row["length"])
        except FileNotFoundError:
            raise HTTPException(503, "mbox file not available")
        for a_idx, filename, mime, payload in iter_attachments(msg):
            if a_idx == idx:
                return {"filename": filename, "mime": mime, "size": len(payload),
                        "text": extract_text(filename, mime, payload)}
        raise HTTPException(404, "attachment not found")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/favicon.ico")
    def favicon():
        # The browser's default /favicon.ico request; serve the SVG logo (no 404).
        return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"),
                            media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
