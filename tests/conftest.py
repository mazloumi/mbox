import io
from email.message import EmailMessage
from email.generator import BytesGenerator

import pytest
from reportlab.pdfgen import canvas
import docx


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _make_docx(text: str) -> bytes:
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph(text)
    d.save(buf)
    return buf.getvalue()


def _email(subject, sender, to, labels, html, attachments):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["X-Gmail-Labels"] = labels
    msg.set_content("plain text body")
    msg.add_alternative(html, subtype="html")
    for filename, maintype, subtype, data in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def _serialize(msg) -> bytes:
    buf = io.BytesIO()
    BytesGenerator(buf).flatten(msg)
    return buf.getvalue()


@pytest.fixture
def sample_mbox(tmp_path):
    pdf = _make_pdf("INVOICE 12345")
    dx = _make_docx("QUARTERLY REPORT")
    m1 = _email(
        "Welcome aboard", "alice@example.com", "bob@example.com",
        "Inbox,Important",
        "<html><body><p>Hello <b>Bob</b></p></body></html>",
        [("invoice.pdf", "application", "pdf", pdf)],
    )
    m2 = _email(
        "Q1 numbers", "carol@example.com", "bob@example.com",
        "Inbox,Work",
        "<html><body><p>See attached report</p></body></html>",
        [("report.docx", "application",
          "vnd.openxmlformats-officedocument.wordprocessingml.document", dx)],
    )
    path = tmp_path / "sample.mbox"
    with open(path, "wb") as f:
        for m in (m1, m2):
            data = _serialize(m)
            f.write(b"From - Mon Jan 01 10:00:00 2024\n")
            f.write(data)
            if not data.endswith(b"\n"):
                f.write(b"\n")
            f.write(b"\n")
    return str(path)
