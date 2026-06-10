import html
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .store import Store
from .reader import read_message, read_message_bytes, iter_attachments, get_display_body
from .sanitize import sanitize_html
from .indexer import build_index, index_is_current
from .status import IndexStatus, EmbedStatus
from . import assets
from . import filetypes
from .assetstore import AssetStore
from .archive import ArchiveStatus, run_archive
from .extract import extract_text, iter_tnef_attachments
from . import embed as embed_mod
from . import retrieve as retrieve_mod
from . import assistant as assistant_mod
from .embed_index import build_chunks, build_embeddings

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


def _zip_entry_name(mid, filename, seen):
    """A safe, unique zip entry name: basename only (strip both / and \\ to avoid
    zip-slip), prefixed by message id, de-collided against `seen`."""
    base = os.path.basename((filename or "file").replace("\\", "/")) or "file"
    name = f"{mid}-{base}"
    if name not in seen:
        seen.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    i = 1
    while True:
        cand = f"{stem}-{i}.{ext}" if dot else f"{name}-{i}"
        if cand not in seen:
            seen.add(cand)
            return cand
        i += 1


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
    store = Store(settings.index_path, enable_vectors=settings.semantic_active())
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

    embed_status = EmbedStatus()
    app.state.embed_status = embed_status
    embedder = embed_mod.make_embedder(settings) if settings.semantic_active() else None
    app.state.embedder = embedder

    _client_box = {}

    def anthropic_client():
        # Lock-free memoization: deliberate for this single-user app — a concurrent
        # first-call race just constructs a throwaway client (no network), harmless.
        if "c" not in _client_box:
            import anthropic  # lazy
            _client_box["c"] = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return _client_box["c"]

    def _run_embed():
        try:
            build_chunks(settings, store, embed_status)
            build_embeddings(settings, store, embedder, embed_status)
        except Exception as exc:  # noqa: BLE001 - surface to the capabilities probe
            sys.stderr.write(f"semantic build failed: {exc}\n")
            embed_status.fail(exc)
        except BaseException as exc:
            embed_status.fail(RuntimeError(f"semantic build interrupted: {exc}"))
            raise

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

    def _index_then_embed():
        _run_index()
        if settings.semantic_active():
            _run_embed()

    if index_is_current(settings, store):
        status.mark_ready(store.message_count())
        if settings.semantic_active():
            if index_in_background:
                threading.Thread(target=_run_embed, daemon=True).start()
            else:
                _run_embed()
    elif index_in_background:
        threading.Thread(target=_index_then_embed, daemon=True).start()
    else:
        _index_then_embed()

    @app.get("/api/status")
    def get_status():
        snap = status.snapshot()
        snap["mbox"] = settings.mbox_name or os.path.basename(settings.mbox_path)
        snap["current"] = index_is_current(settings, store)
        return snap

    def _semantic_ready():
        return settings.semantic_active() and embed_status.snapshot()["ready"]

    @app.get("/api/capabilities")
    def capabilities():
        snap = embed_status.snapshot()
        sem_on = settings.semantic_active()
        asst_on = settings.assistant_active()
        return {
            "semantic": {
                "enabled": sem_on,
                "ready": sem_on and snap["ready"],
                "state": snap["state"] if sem_on else "off",
                "messages_done": snap["messages_done"],
                "messages_total": snap["messages_total"],
                "vectors_done": snap["vectors_done"],
                "vectors_total": snap["vectors_total"],
            },
            "assistant": {
                "enabled": asst_on,
                "ready": asst_on and snap["ready"],
                "model": settings.gen_model if asst_on else None,
            },
        }

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
               sort: str = "date_desc", mode: str = "keyword"):
        offset = (page - 1) * page_size
        if mode == "hybrid" and _semantic_ready() and not label and page == 1 \
                and not (date_from or date_to or from_q or has_attachment):
            rows = retrieve_mod.search(store, embedder, q, page_size)
            return {"messages": [_msg_summary(r) for r in rows], "page": page,
                    "mode": "hybrid"}
        rows = store.search(q, label, page_size, offset,
                            date_from=date_from, date_to=date_to, from_q=from_q,
                            has_attachment=has_attachment, sort=sort)
        return {"messages": [_msg_summary(r) for r in rows], "page": page,
                "mode": "keyword"}

    @app.post("/api/assistant/chat")
    def assistant_chat(payload: dict):
        if not settings.assistant_active():
            raise HTTPException(404, "assistant not enabled")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(400, "messages must be a non-empty list")
        last = messages[-1]
        if (not isinstance(last, dict) or last.get("role") != "user"
                or not isinstance(last.get("content"), str) or not last["content"].strip()):
            raise HTTPException(400, "last message must be a user turn with text content")
        question = last["content"]
        history = messages[:-1]

        def _ndjson():
            snap = embed_status.snapshot()
            if not snap["ready"]:
                if snap["state"] == "error":
                    yield json.dumps({"type": "error",
                                      "error": "knowledge base build failed; check server logs"}) + "\n"
                else:
                    total = snap["vectors_total"] or 1
                    pct = round(snap["vectors_done"] / total * 100, 1)
                    yield json.dumps({"type": "building", "percent": pct}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"
                return
            snippets = retrieve_mod.retrieve_context(store, embedder, question)
            yield json.dumps({"type": "sources",
                              "sources": assistant_mod.sources_for(snippets)}) + "\n"
            client = anthropic_client()
            generate = assistant_mod.make_anthropic_generate(client, settings.gen_model)
            try:
                for text in assistant_mod.iter_answer(generate, history, question, snippets):
                    yield json.dumps({"type": "token", "text": text}) + "\n"
            except Exception as exc:  # noqa: BLE001
                yield json.dumps({"type": "error", "error": str(exc)}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"

        return StreamingResponse(_ndjson(), media_type="application/x-ndjson")

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
        msg_cache = {}   # message_id -> parsed EmailMessage (read/parsed once per message)
        seen = set()     # de-collide zip entry names
        spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        try:
            with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in rows:
                    mid = r["message_id"]
                    msg = msg_cache.get(mid)
                    if msg is None:
                        mrow = store.get_message_row(mid)
                        if mrow is None:
                            continue
                        msg = read_message(settings.mbox_path, mrow["offset"], mrow["length"])
                        msg_cache[mid] = msg
                    payload = fname = None
                    for a_idx, filename, _mime, p in iter_attachments(msg):
                        if a_idx == r["idx"]:
                            payload, fname = p, filename
                            break
                    if payload is None:
                        continue
                    if total + len(payload) > CAP_BYTES:
                        break   # stop entirely once the next file would exceed the cap
                    zf.writestr(_zip_entry_name(mid, fname, seen), payload)
                    total += len(payload)
        except FileNotFoundError:
            spool.close()
            raise HTTPException(503, "mbox file not available")
        except Exception:
            spool.close()
            raise
        spool.seek(0)
        label = category or "search"

        def _stream():
            # Chunked read so the (≤1 GB) zip is never fully re-materialized in RAM.
            try:
                while True:
                    chunk = spool.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                spool.close()

        return StreamingResponse(
            _stream(), media_type="application/zip",
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
