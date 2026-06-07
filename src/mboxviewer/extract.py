import io
from html.parser import HTMLParser

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html or "")
    return " ".join("".join(p.parts).split())


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    return "\n".join(par.text for par in d.paragraphs)


def extract_text(filename: str, mime: str, data: bytes) -> str:
    """Best-effort plain-text extraction. Returns '' for unsupported or on any error."""
    mime = (mime or "").lower()
    try:
        if mime == "application/pdf":
            return _pdf_text(data)
        if mime == _DOCX_MIME:
            return _docx_text(data)
        if mime.startswith("text/html"):
            return html_to_text(data.decode("utf-8", "replace"))
        if mime.startswith("text/"):
            return data.decode("utf-8", "replace")
    except Exception:
        return ""
    return ""
