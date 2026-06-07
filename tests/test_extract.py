import io
import docx
from reportlab.pdfgen import canvas
from mboxviewer.extract import extract_text, html_to_text


def _pdf(text):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _docx(text):
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph(text)
    d.save(buf)
    return buf.getvalue()


def test_extract_plain_text():
    assert "hello" in extract_text("a.txt", "text/plain", b"hello world")


def test_extract_pdf():
    out = extract_text("a.pdf", "application/pdf", _pdf("INVOICE 12345"))
    assert "12345" in out


def test_extract_docx():
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    out = extract_text("a.docx", mime, _docx("QUARTERLY REPORT"))
    assert "QUARTERLY" in out


def test_extract_unsupported_returns_empty():
    assert extract_text("a.bin", "application/octet-stream", b"\x00\x01") == ""


def test_extract_handles_corrupt_pdf_gracefully():
    assert extract_text("a.pdf", "application/pdf", b"not a real pdf") == ""


def test_html_to_text_strips_tags():
    assert html_to_text("<p>Hello <b>world</b></p>").strip() == "Hello world"
