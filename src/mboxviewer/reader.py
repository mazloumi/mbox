import email
import re
from email import policy


def iter_message_spans(path):
    """Yield (offset, length) byte spans for each message in an mbox file.

    A message boundary is a line starting with b'From ' that is at the start of
    the file or immediately preceded by a blank line (mboxrd convention).
    """
    with open(path, "rb") as f:
        msg_start = None
        prev_blank = True
        pos = 0
        while True:
            line = f.readline()
            if not line:
                break
            if line.startswith(b"From ") and prev_blank:
                if msg_start is not None:
                    yield (msg_start, pos - msg_start)
                msg_start = pos
            prev_blank = line in (b"\n", b"\r\n")
            pos += len(line)
        if msg_start is not None:
            yield (msg_start, pos - msg_start)


def read_message_bytes(path, offset, length):
    """Return the raw RFC-822 bytes for a message span.

    Strips the leading ``From …`` envelope line and un-escapes mboxrd
    ``>From`` → ``From`` so callers receive a pure RFC-822 / .eml payload.
    """
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(length)
    nl = raw.find(b"\n")
    if raw.startswith(b"From ") and nl != -1:
        raw = raw[nl + 1:]
    return re.sub(rb"(?m)^>(>*From )", rb"\1", raw)


def read_message(path, offset, length):
    return email.message_from_bytes(
        read_message_bytes(path, offset, length), policy=policy.default
    )


def parse_labels(header_value):
    if not header_value:
        return []
    return [p.strip() for p in str(header_value).split(",") if p.strip()]


def iter_attachments(msg):
    """Yield (idx, filename, mime, payload_bytes) for attachment parts in walk order."""
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or (filename and disposition != "inline"):
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            yield idx, filename or f"attachment-{idx}", part.get_content_type(), payload
            idx += 1


def get_display_body(msg):
    """Return (mime, content) preferring HTML, falling back to plain text."""
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ("text/plain", "")
    try:
        return (body.get_content_type(), body.get_content())
    except Exception:
        return ("text/plain", "")
