import io
from email.message import EmailMessage
from email.generator import BytesGenerator

from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.assetstore import AssetStore
from mboxviewer.archive import ArchiveStatus, run_archive
from mboxviewer.assets import url_hash, read_asset_bytes


def _mbox_with_html(tmp_path, html):
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body"); m.add_alternative(html, subtype="html")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "a.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    return str(p)


def _setup(tmp_path, mbox):
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=mbox, index_path=str(tmp_path / "i.db"),
                        archive_dir=str(tmp_path / "arch"))
    store = Store(settings.index_path); store.create_schema(); build_index(settings, store)
    asset_store = AssetStore(settings.archive_dir); asset_store.create_schema()
    return settings, store, asset_store


def test_archive_downloads_real_and_skips_tracker(tmp_path, image_server):
    base, requested = image_server
    html = f'<img src="{base}/logo.png"><img src="{base}/pixel.gif" width="1" height="1">'
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, html))
    status = ArchiveStatus()
    run_archive(settings, store, astore, status)
    s = status.snapshot()
    assert s["running"] is False and s["error"] is None
    assert s["downloaded"] == 1 and s["skipped"] == 1
    logo_h = url_hash(f"{base}/logo.png")
    assert astore.asset_status(logo_h) == "ok"
    assert read_asset_bytes(settings.archive_dir, logo_h) == b"FAKEIMAGEBYTES"
    assert astore.asset_status(url_hash(f"{base}/pixel.gif")) == "skipped"
    assert "/logo.png" in requested and "/pixel.gif" not in requested


def test_archive_records_failed(tmp_path, image_server):
    base, _ = image_server
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, f'<img src="{base}/notimage.html">'))
    run_archive(settings, store, astore, ArchiveStatus())
    assert astore.asset_status(url_hash(f"{base}/notimage.html")) == "failed"


def test_archive_resumable_and_short_circuits(tmp_path, image_server):
    base, requested = image_server
    settings, store, astore = _setup(tmp_path, _mbox_with_html(tmp_path, f'<img src="{base}/logo.png">'))
    run_archive(settings, store, astore, ArchiveStatus())
    requested.clear()
    status2 = ArchiveStatus()
    run_archive(settings, store, astore, status2)
    assert requested == []
    s = status2.snapshot()
    assert s["running"] is False and s["messages_scanned"] == 0 and s["downloaded"] == 1
