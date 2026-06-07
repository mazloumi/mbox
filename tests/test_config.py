from mboxviewer.config import load_settings


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("MBOX_PATH", "/data/x.mbox")
    monkeypatch.setenv("INDEX_PATH", "/index/i.db")
    monkeypatch.setenv("PORT", "9000")
    s = load_settings()
    assert s.mbox_path == "/data/x.mbox"
    assert s.index_path == "/index/i.db"
    assert s.port == 9000
    assert s.host == "0.0.0.0"


def test_archive_dir_default_and_env(monkeypatch):
    monkeypatch.delenv("ARCHIVE_DIR", raising=False)
    assert load_settings().archive_dir == "/archive"
    monkeypatch.setenv("ARCHIVE_DIR", "/tmp/arch")
    assert load_settings().archive_dir == "/tmp/arch"
