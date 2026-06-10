import json

from fastapi.testclient import TestClient

from mboxviewer.config import Settings
from mboxviewer.api import create_app


def _app(tmp_path, sample_mbox, **kw):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"), **kw)
    return create_app(settings, index_in_background=False)


def test_capabilities_off_by_default(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    caps = c.get("/api/capabilities").json()
    assert caps["semantic"]["enabled"] is False
    assert caps["assistant"]["enabled"] is False


def test_chat_404_when_disabled(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    r = c.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


def test_search_keyword_mode_unaffected(tmp_path, sample_mbox):
    c = TestClient(_app(tmp_path, sample_mbox))
    r = c.get("/api/search", params={"q": "Welcome"})
    assert r.status_code == 200
    assert any(m["subject"] == "Welcome aboard" for m in r.json()["messages"])


def test_semantic_tier_builds_and_serves(tmp_path, sample_mbox, monkeypatch):
    from mboxviewer import embed

    class FakeEmbedder:
        model_name = "fake"
        dim = 4
        def embed_texts(self, texts):
            return [[float(sum(map(ord, t)) % 7), float(len(t) % 5), 1.0, 0.0] for t in texts]

    monkeypatch.setattr(embed, "make_embedder", lambda settings: FakeEmbedder())

    app = _app(tmp_path, sample_mbox, semantic_search_enabled=True)
    c = TestClient(app)
    caps = c.get("/api/capabilities").json()
    assert caps["semantic"]["enabled"] is True
    r = c.get("/api/search", params={"q": "report", "mode": "hybrid"})
    assert r.status_code == 200


def test_chat_streams_with_fake_client(tmp_path, sample_mbox, monkeypatch):
    from mboxviewer import embed, assistant

    class FakeEmbedder:
        model_name = "fake"; dim = 4
        def embed_texts(self, texts):
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "make_embedder", lambda settings: FakeEmbedder())
    monkeypatch.setattr(assistant, "make_anthropic_generate",
                        lambda client, model: (lambda system, messages: iter(["Answer ", "[#1]"])))

    app = _app(tmp_path, sample_mbox, assistant_enabled=True, anthropic_api_key="sk-ant-test")
    c = TestClient(app)
    with c.stream("POST", "/api/assistant/chat",
                  json={"messages": [{"role": "user", "content": "what was sent?"}]}) as r:
        assert r.status_code == 200
        lines = [json.loads(ln) for ln in r.iter_lines() if ln]
    types = [d["type"] for d in lines]
    assert types[0] == "sources"
    assert "token" in types
    assert types[-1] == "done"
    assert "".join(d["text"] for d in lines if d["type"] == "token") == "Answer [#1]"


def test_chat_400_on_malformed_last_turn(tmp_path, sample_mbox, monkeypatch):
    from mboxviewer import embed
    class FakeEmbedder:
        model_name = "fake"; dim = 4
        def embed_texts(self, texts): return [[1.0, 0.0, 0.0, 0.0] for _ in texts]
    monkeypatch.setattr(embed, "make_embedder", lambda settings: FakeEmbedder())
    app = _app(tmp_path, sample_mbox, assistant_enabled=True, anthropic_api_key="sk-ant-test")
    c = TestClient(app)
    assert c.post("/api/assistant/chat", json={"messages": [{"role": "user"}]}).status_code == 400
    assert c.post("/api/assistant/chat", json={"messages": []}).status_code == 400
    # non-list messages value must return 400 (not 500 TypeError)
    assert c.post("/api/assistant/chat", json={"messages": 123}).status_code == 400
    # whitespace-only content must return 400
    assert c.post("/api/assistant/chat",
                  json={"messages": [{"role": "user", "content": "  "}]}).status_code == 400
