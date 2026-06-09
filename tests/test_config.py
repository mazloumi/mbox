import importlib

from mboxviewer.config import Settings, load_settings


def _settings(**kw):
    base = dict(mbox_path="/x.mbox", index_path="/i.db")
    base.update(kw)
    return Settings(**base)


def test_defaults_off():
    s = _settings()
    assert s.semantic_search_enabled is False
    assert s.assistant_enabled is False
    assert s.anthropic_api_key is None
    assert s.gen_model == "claude-sonnet-4-6"
    assert s.embed_backend == "local"
    assert s.semantic_active() is False
    assert s.assistant_active() is False


def test_assistant_needs_key():
    s = _settings(assistant_enabled=True, anthropic_api_key=None)
    assert s.assistant_active() is False
    assert s.semantic_active() is False


def test_assistant_active_implies_semantic():
    s = _settings(assistant_enabled=True, anthropic_api_key="sk-ant-xyz")
    assert s.assistant_active() is True
    assert s.semantic_active() is True


def test_semantic_standalone():
    s = _settings(semantic_search_enabled=True)
    assert s.semantic_active() is True
    assert s.assistant_active() is False


def test_archive_dir_default_and_env(monkeypatch):
    monkeypatch.delenv("ARCHIVE_DIR", raising=False)
    assert load_settings().archive_dir == "/archive"
    monkeypatch.setenv("ARCHIVE_DIR", "/tmp/arch")
    assert load_settings().archive_dir == "/tmp/arch"


def test_load_settings_reads_env(monkeypatch):
    # Base env wiring.
    monkeypatch.setenv("MBOX_PATH", "/data/x.mbox")
    monkeypatch.setenv("INDEX_PATH", "/index/i.db")
    monkeypatch.setenv("PORT", "9000")
    # Assistant / semantic-search env wiring.
    monkeypatch.setenv("SEMANTIC_SEARCH", "1")
    monkeypatch.setenv("ASSISTANT_ENABLED", "yes")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    monkeypatch.setenv("ASSISTANT_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("EMBED_BACKEND", "ollama")
    import mboxviewer.config as cfg
    importlib.reload(cfg)
    s = cfg.load_settings()
    # Base fields.
    assert s.mbox_path == "/data/x.mbox"
    assert s.index_path == "/index/i.db"
    assert s.port == 9000
    assert s.host == "0.0.0.0"
    # Assistant / semantic-search fields.
    assert s.semantic_search_enabled is True
    assert s.assistant_enabled is True
    assert s.gen_model == "claude-opus-4-8"
    assert s.embed_backend == "ollama"
    assert s.assistant_active() is True
