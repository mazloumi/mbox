import time

import pytest
from fastapi.testclient import TestClient
from mboxviewer.config import Settings
from mboxviewer.api import create_app, _render_body, _content_disposition


@pytest.fixture
def client(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    return TestClient(create_app(settings, index_in_background=False))


def test_labels_endpoint(client):
    data = client.get("/api/labels").json()
    by_name = {d["name"]: d["count"] for d in data}
    assert by_name["Inbox"] == 2 and by_name["Work"] == 1


def test_messages_listing_for_label(client):
    data = client.get("/api/messages", params={"label": "Important"}).json()
    assert len(data["messages"]) == 1
    assert data["messages"][0]["subject"] == "Welcome aboard"


def test_message_detail_sanitizes_body(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    detail = client.get(f"/api/messages/{mid}").json()
    assert "<b>Bob</b>" in detail["body_html"]
    assert detail["attachments"][0]["filename"] == "invoice.pdf"


def test_attachment_download(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    resp = client.get(f"/api/messages/{mid}/attachments/0")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"
    assert "invoice.pdf" in resp.headers["content-disposition"]


def test_search_finds_attachment_text(client):
    data = client.get("/api/search", params={"q": "12345"}).json()
    assert len(data["messages"]) == 1


def test_index_html_served(client):
    assert client.get("/").status_code == 200


def test_render_body_escapes_plain_text():
    out = _render_body("text/plain", "List<String> items")
    assert "&lt;String&gt;" in out and "String" in out


def test_render_body_sanitizes_html():
    out = _render_body("text/html", "<b>hi</b><script>bad()</script>")
    assert "<b>hi</b>" in out and "bad" not in out


def test_content_disposition_ascii():
    assert _content_disposition("invoice.pdf") == 'attachment; filename="invoice.pdf"'


def test_content_disposition_escapes_quote():
    assert '\\"' in _content_disposition('a"b.pdf')


def test_content_disposition_non_ascii():
    out = _content_disposition("résumé.pdf")
    assert "filename*=UTF-8''" in out and "r%C3%A9sum%C3%A9.pdf" in out


def test_status_ready_after_sync_index(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    c = TestClient(create_app(settings, index_in_background=False))
    s = c.get("/api/status").json()
    assert s["ready"] is True and s["indexing"] is False
    assert s["messages"] == 2 and s["percent"] == 100.0 and s["error"] is None


def test_status_background_eventually_ready(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    c = TestClient(create_app(settings))  # background (default)
    s = {}
    for _ in range(100):
        s = c.get("/api/status").json()
        if s["ready"]:
            break
        time.sleep(0.05)
    assert s["ready"] is True and s["messages"] == 2


def test_status_ready_on_reused_index(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    TestClient(create_app(settings, index_in_background=False))  # build once
    c = TestClient(create_app(settings, index_in_background=False))  # reuse
    s = c.get("/api/status").json()
    assert s["ready"] is True and s["messages"] == 2


def test_attachment_inline_disposition(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    r = client.get(f"/api/messages/{mid}/attachments/0", params={"inline": "true"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_attachment_default_disposition(client):
    mid = client.get("/api/messages", params={"label": "Important"}).json()["messages"][0]["id"]
    r = client.get(f"/api/messages/{mid}/attachments/0")
    assert r.headers["content-disposition"].startswith("attachment")


def test_content_disposition_inline_flag():
    assert _content_disposition("a.pdf", inline=True).startswith('inline; filename="a.pdf"')
    assert _content_disposition("a.pdf").startswith("attachment;")


def test_inline_forced_to_attachment_for_unsafe_mime(tmp_path):
    import io
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(b"<script>alert(1)</script>", maintype="text", subtype="html",
                     filename="evil.html")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "h.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"))
    c = TestClient(create_app(settings, index_in_background=False))
    mid = c.get("/api/messages").json()["messages"][0]["id"]
    r = c.get(f"/api/messages/{mid}/attachments/0", params={"inline": "true"})
    assert r.headers["content-disposition"].startswith("attachment")  # forced, not inline
    assert r.headers["x-content-type-options"] == "nosniff"
