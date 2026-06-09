"""Pure text → chunks. No I/O. A chunk is a window of text prefixed with a compact
header so a retrieved snippet still says which message/attachment it came from."""
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

WINDOW = 2000          # chars per chunk (~512 tokens at ~4 chars/token)
OVERLAP = 200          # chars of overlap between consecutive windows
ATTACH_CAP = 20        # max chunks per attachment (bounds embedding cost)


@dataclass
class Chunk:
    kind: str                 # "body" | "attachment"
    ord: int                  # order within (message, kind, source)
    source_idx: Optional[int] # attachment idx for kind="attachment", else None
    text: str                 # header line + windowed body text


def _windows(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= WINDOW:
        return [text]
    out = []
    step = WINDOW - OVERLAP
    start = 0
    while start < len(text):
        out.append(text[start:start + WINDOW])
        start += step
    return out


def iter_chunks(subject: str, from_addr: str, date: str, body: str,
                attachments: Iterable[Tuple[int, str, str]]) -> Iterator[Chunk]:
    """Yield Chunks for one message.

    `attachments` is an iterable of (idx, filename, extracted_text).
    """
    head = " · ".join(p for p in (subject, from_addr, date) if p)
    for i, win in enumerate(_windows(body)):
        yield Chunk("body", i, None, f"{head}\n{win}")
    for idx, filename, text in attachments:
        wins = _windows(text)[:ATTACH_CAP]
        ahead = " · ".join(p for p in (subject, from_addr, date, filename) if p)
        for i, win in enumerate(wins):
            yield Chunk("attachment", i, idx, f"{ahead}\n{win}")
