import io as _io
import os as _os
import time
from email.message import EmailMessage
from email.generator import BytesGenerator

import pytest
from fastapi.testclient import TestClient
from mboxviewer.config import Settings
from mboxviewer.api import create_app, _render_body, _content_disposition


@pytest.fixture
def client(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    return TestClient(create_app(settings, index_in_background=False))


def _client_for_html(tmp_path, html):
    m = EmailMessage()
    m["Subject"] = "img"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body"); m.add_alternative(html, subtype="html")
    buf = _io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "img.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    return TestClient(create_app(settings, index_in_background=False)), settings


def test_archive_status_idle(client):
    s = client.get("/api/archive/status").json()
    assert s["running"] is False and s["downloaded"] == 0


def test_archive_start_returns_started(client):
    assert client.post("/api/archive/start").json()["started"] in (True, False)


def test_asset_endpoint_serves_cached_bytes(tmp_path):
    from mboxviewer import assets
    c, settings = _client_for_html(tmp_path, '<p>hi</p>')
    h = assets.url_hash("https://x.example/logo.png")
    astore = c.app.state.asset_store
    assets.write_asset_bytes(settings.archive_dir, h, b"IMGDATA")
    astore.upsert_asset(h, "https://x.example/logo.png", "image/png", 7, None, None, "ok", None, "t")
    astore.commit()
    r = c.get(f"/api/asset/{h}")
    assert r.status_code == 200 and r.content == b"IMGDATA"
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/api/asset/" + "0" * 64).status_code == 404
    assert c.get("/api/asset/not-hex").status_code == 404


def test_message_detail_rewrites_cached_image(tmp_path):
    from mboxviewer import assets
    url = "https://x.example/logo.png"
    c, settings = _client_for_html(tmp_path, f'<img src="{url}">')
    h = assets.url_hash(url)
    astore = c.app.state.asset_store
    assets.write_asset_bytes(settings.archive_dir, h, b"IMGDATA")
    astore.upsert_asset(h, url, "image/png", 7, None, None, "ok", None, "t")
    astore.commit()
    mid = c.get("/api/messages", params={"label": "Inbox"}).json()["messages"][0]["id"]
    body = c.get(f"/api/messages/{mid}").json()["body_html"]
    assert f"/api/asset/{h}" in body and url not in body


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
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    s = c.get("/api/status").json()
    assert s["ready"] is True and s["indexing"] is False
    assert s["messages"] == 2 and s["percent"] == 100.0 and s["error"] is None


def test_status_background_eventually_ready(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings))  # background (default)
    s = {}
    for _ in range(100):
        s = c.get("/api/status").json()
        if s["ready"]:
            break
        time.sleep(0.05)
    assert s["ready"] is True and s["messages"] == 2


def test_status_ready_on_reused_index(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
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
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    mid = c.get("/api/messages").json()["messages"][0]["id"]
    r = c.get(f"/api/messages/{mid}/attachments/0", params={"inline": "true"})
    assert r.headers["content-disposition"].startswith("attachment")  # forced, not inline
    assert r.headers["x-content-type-options"] == "nosniff"


def test_inline_allows_safe_media_and_bmp(tmp_path):
    import io
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(b"ID3audio", maintype="audio", subtype="mpeg", filename="a.mp3")
    m.add_attachment(b"\x00\x00\x00mp4", maintype="video", subtype="mp4", filename="v.mp4")
    m.add_attachment(b"BM\x00\x00", maintype="image", subtype="bmp", filename="i.bmp")
    m.add_attachment(b"<b>x</b>", maintype="text", subtype="html", filename="e.html")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "m.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    mid = c.get("/api/messages").json()["messages"][0]["id"]
    # attachments are walk-ordered: 0=mp3, 1=mp4, 2=bmp, 3=html
    for idx in (0, 1, 2):
        r = c.get(f"/api/messages/{mid}/attachments/{idx}", params={"inline": "true"})
        assert r.headers["content-disposition"].startswith("inline"), idx
        assert r.headers["x-content-type-options"] == "nosniff"
    # text/html still forced to attachment (allowlist must not have widened unsafely)
    r = c.get(f"/api/messages/{mid}/attachments/3", params={"inline": "true"})
    assert r.headers["content-disposition"].startswith("attachment")


def test_status_mbox_name_override(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"), mbox_name="your-mail.mbox")
    c = TestClient(create_app(settings, index_in_background=False))
    assert c.get("/api/status").json()["mbox"] == "your-mail.mbox"


def test_status_includes_mbox_and_current(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    c = TestClient(create_app(settings, index_in_background=False))
    s = c.get("/api/status").json()
    assert s["mbox"] == _os.path.basename(sample_mbox)
    assert s["current"] is True              # just indexed -> matches the mbox
    _os.utime(sample_mbox, (0, 0))           # change the mbox mtime
    assert c.get("/api/status").json()["current"] is False


def test_filetypes_endpoint(client):
    cats = {c["category"]: c["count"] for c in client.get("/api/filetypes").json()}
    assert cats["Documents"] == 2          # sample has invoice.pdf + report.docx


def test_files_by_category(client):
    data = client.get("/api/files", params={"category": "Documents"}).json()
    names = sorted(f["filename"] for f in data["files"])
    assert names == ["invoice.pdf", "report.docx"]
    assert all("subject" in f and "size" in f and "idx" in f for f in data["files"])


def test_files_unknown_category_empty(client):
    assert client.get("/api/files", params={"category": "Nope"}).json()["files"] == []


def test_files_unknown_category_with_query_still_empty(client):
    # An unknown category with a query must NOT fall through to a global search.
    assert client.get("/api/files", params={"category": "Nope", "q": "invoice"}).json()["files"] == []


def test_file_text_endpoint(client):
    data = client.get("/api/files", params={"category": "Documents"}).json()
    pdf = next(f for f in data["files"] if f["filename"] == "invoice.pdf")
    t = client.get(f"/api/files/{pdf['message_id']}/{pdf['idx']}/text").json()
    assert t["filename"] == "invoice.pdf" and "12345" in t["text"]
    assert client.get("/api/files/999999/0/text").status_code == 404


def test_files_search_within_category(client):
    data = client.get("/api/files", params={"category": "Documents", "q": "invoice"}).json()
    assert [f["filename"] for f in data["files"]] == ["invoice.pdf"]


def test_files_search_no_category(client):
    data = client.get("/api/files", params={"q": "invoice"}).json()
    assert any(f["filename"] == "invoice.pdf" for f in data["files"])


def test_files_no_category_no_query_empty(client):
    assert client.get("/api/files").json()["files"] == []


def test_archive_status_includes_persisted_state(tmp_path, image_server):
    from mboxviewer.archive import ArchiveStatus, run_archive
    base, _ = image_server
    c, settings = _client_for_html(tmp_path, f'<img src="{base}/logo.png">')
    before = c.get("/api/archive/status").json()
    assert before["archived"]["total"] == 0 and before["up_to_date"] is False
    run_archive(settings, c.app.state.store, c.app.state.asset_store, ArchiveStatus())
    after = c.get("/api/archive/status").json()
    assert after["archived"]["ok"] == 1 and after["up_to_date"] is True
