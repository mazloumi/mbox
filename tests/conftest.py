import io
import threading
from email.message import EmailMessage
from email.generator import BytesGenerator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


@pytest.fixture
def image_server():
    """Stub HTTP server. Records requested paths. /notimage* -> text/html,
    /big* -> 11MB image, else -> small image/png bytes."""
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if self.path.startswith("/notimage"):
                body, ctype = b"<html>nope</html>", "text/html"
            elif self.path.startswith("/svg"):
                body, ctype = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"
            elif self.path.startswith("/big"):
                body, ctype = b"x" * (11 * 1024 * 1024), "image/png"
            else:
                body, ctype = b"FAKEIMAGEBYTES", "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class QuietServer(ThreadingHTTPServer):
        # Suppress the broken-pipe traceback when a client (e.g. the oversize test)
        # disconnects mid-response.
        def handle_error(self, request, client_address):
            pass

    srv = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    yield (f"http://127.0.0.1:{port}", requested)
    srv.shutdown()
