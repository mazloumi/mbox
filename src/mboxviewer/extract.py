import io
from html.parser import HTMLParser

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_XLS_MIME = "application/vnd.ms-excel"


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


def _pptx_text(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            elif shape.has_table:  # text-frame check misses table cells
                for trow in shape.table.rows:
                    parts.append("\t".join(cell.text for cell in trow.cells))
    return "\n".join(p for p in parts if p and p.strip())


def _cell_str(c) -> str:
    # Spreadsheet libs hand back whole numbers as floats (999 -> 999.0); render
    # those as plain integers so the text reads naturally and searches cleanly.
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c)


def _rows_text(rows) -> str:
    out = []
    for row in rows:
        cells = [s for s in (_cell_str(c) for c in row if c is not None) if s.strip()]
        if cells:
            out.append("\t".join(cells))
    return "\n".join(out)


def _xlsx_text(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return "\n".join(_rows_text(ws.iter_rows(values_only=True)) for ws in wb.worksheets)
    finally:
        wb.close()


def _xls_text(data: bytes) -> str:
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    sheets = []
    for sheet in book.sheets():
        rows = (sheet.row_values(r) for r in range(sheet.nrows))
        sheets.append(_rows_text(rows))
    return "\n".join(sheets)


def extract_text(filename: str, mime: str, data: bytes) -> str:
    """Best-effort plain-text extraction. Returns '' for unsupported or on any error."""
    mime = (mime or "").lower()
    try:
        if mime == "application/pdf":
            return _pdf_text(data)
        if mime == _DOCX_MIME:
            return _docx_text(data)
        if mime == _PPTX_MIME:
            return _pptx_text(data)
        if mime == _XLSX_MIME:
            return _xlsx_text(data)
        if mime == _XLS_MIME:
            return _xls_text(data)
        if mime.startswith("text/html"):
            return html_to_text(data.decode("utf-8", "replace"))
        if mime.startswith("text/"):
            return data.decode("utf-8", "replace")
    except Exception:
        return ""
    return ""
