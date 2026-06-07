import pytest
from fastapi.testclient import TestClient
from mboxviewer.config import Settings
from mboxviewer.api import create_app


@pytest.fixture
def client(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    return TestClient(create_app(settings))


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
