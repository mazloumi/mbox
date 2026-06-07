from mboxviewer.assetstore import AssetStore


def _astore(tmp_path):
    a = AssetStore(str(tmp_path / "arch"))
    a.create_schema()
    return a


def test_upsert_and_lookup(tmp_path):
    a = _astore(tmp_path)
    a.upsert_asset("h1", "https://x/a.png", "image/png", 10, 100, 50, "ok", None, "t")
    a.commit()
    assert a.asset_status("h1") == "ok"
    assert a.asset_status("nope") is None
    assert a.get_asset("h1")["content_type"] == "image/png"
    a.upsert_asset("h1", "https://x/a.png", "image/png", 10, 100, 50, "failed", "boom", "t2")
    a.commit()
    assert a.asset_status("h1") == "failed"


def test_cached_hashes_and_counts(tmp_path):
    a = _astore(tmp_path)
    a.upsert_asset("ok1", "u1", "image/png", 1, None, None, "ok", None, "t")
    a.upsert_asset("ok2", "u2", "image/png", 1, None, None, "ok", None, "t")
    a.upsert_asset("sk1", "u3", None, None, 1, 1, "skipped", None, "t")
    a.upsert_asset("fa1", "u4", None, None, None, None, "failed", "x", "t")
    a.commit()
    assert a.cached_asset_hashes({"ok1", "fa1", "missing"}) == {"ok1"}
    assert a.cached_asset_hashes(set()) == set()
    assert a.asset_counts() == {"ok": 2, "skipped": 1, "failed": 1, "gave_up": 0, "total": 4}


def test_attempts_and_gave_up_count(tmp_path):
    a = AssetStore(str(tmp_path / "arch"))
    a.create_schema()
    a.upsert_asset("h", "u", None, None, None, None, "failed", "x", "t", attempts=2)
    a.commit()
    assert a.get_attempts("h") == 2
    assert a.get_attempts("missing") == 0
    a.upsert_asset("g", "u2", None, None, None, None, "gave_up", "y", "t", attempts=3)
    a.commit()
    assert a.asset_counts() == {"ok": 0, "skipped": 0, "failed": 1, "gave_up": 1, "total": 2}


def test_migration_adds_attempts_to_old_db(tmp_path):
    import os
    import sqlite3
    d = str(tmp_path / "arch")
    os.makedirs(d, exist_ok=True)
    # Simulate a pre-existing archive.db WITHOUT the attempts column.
    conn = sqlite3.connect(os.path.join(d, "archive.db"))
    conn.execute(
        "CREATE TABLE assets (url_hash TEXT PRIMARY KEY, url TEXT, content_type TEXT,"
        " size INTEGER, width INTEGER, height INTEGER, status TEXT NOT NULL, error TEXT,"
        " fetched_at TEXT)")
    conn.execute("INSERT INTO assets(url_hash, status) VALUES('h', 'ok')")
    conn.commit(); conn.close()
    a = AssetStore(d)
    a.create_schema()                      # must ALTER-add attempts with no data loss
    assert a.get_attempts("h") == 0        # existing row migrated to default 0
    assert a.asset_status("h") == "ok"


def test_archive_meta(tmp_path):
    a = _astore(tmp_path)
    assert a.get_archive_meta() is None
    a.set_archive_meta(12345, 67890)
    assert a.get_archive_meta() == {"source_size": 12345, "source_mtime": 67890}
