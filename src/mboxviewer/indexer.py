import os
from email.utils import parsedate_to_datetime

from .reader import (
    iter_message_spans, read_message, iter_attachments, get_display_body, parse_labels,
)
from .extract import extract_text, html_to_text


def _iso_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return None


def _body_text(msg):
    mime, content = get_display_body(msg)
    return html_to_text(content) if mime == "text/html" else content


def build_index(settings, store, progress=None):
    count = 0
    for offset, length in iter_message_spans(settings.mbox_path):
        try:
            msg = read_message(settings.mbox_path, offset, length)
            date_raw = msg["date"]
            mid = store.add_message(
                offset, length, msg["message-id"], msg["subject"],
                msg["from"], msg["to"], _iso_date(date_raw), date_raw)
            for name in parse_labels(msg["x-gmail-labels"]):
                store.link_label(mid, store.add_label(name))
            att_texts = []
            for idx, filename, mime, payload in iter_attachments(msg):
                store.add_attachment(mid, idx, filename, mime, len(payload))
                att_texts.append(extract_text(filename, mime, payload))
            store.add_fts(
                mid, msg["subject"] or "", msg["from"] or "", msg["to"] or "",
                _body_text(msg), "\n".join(att_texts))
            count += 1
            if progress and count % 500 == 0:
                progress(count)
        except Exception as exc:
            print(f"skipping message at offset {offset}: {exc}")
    store.set_meta("source_size", str(os.path.getsize(settings.mbox_path)))
    store.set_meta("source_mtime", str(int(os.path.getmtime(settings.mbox_path))))
    store.commit()
    return count


def index_is_current(settings, store):
    try:
        size = store.get_meta("source_size")
        mtime = store.get_meta("source_mtime")
    except Exception:
        return False
    if size is None or mtime is None:
        return False
    return (size == str(os.path.getsize(settings.mbox_path))
            and mtime == str(int(os.path.getmtime(settings.mbox_path))))
