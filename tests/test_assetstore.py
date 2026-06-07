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
    assert a.asset_counts() == {"ok": 2, "skipped": 1, "failed": 1, "total": 4}


def test_archive_meta(tmp_path):
    a = _astore(tmp_path)
    assert a.get_archive_meta() is None
    a.set_archive_meta(12345, 67890)
    assert a.get_archive_meta() == {"source_size": 12345, "source_mtime": 67890}
