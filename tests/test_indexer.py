import os
from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.indexer import build_index, index_is_current


def _build(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    count = build_index(settings, store)
    return settings, store, count


def test_build_index_counts_messages(tmp_path, sample_mbox):
    _, store, count = _build(tmp_path, sample_mbox)
    assert count == 2


def test_index_creates_labels_with_counts(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    labels = dict(store.list_labels())
    assert labels["Inbox"] == 2
    assert labels["Important"] == 1
    assert labels["Work"] == 1


def test_index_enables_attachment_text_search(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    assert len(store.search("12345", None, 10, 0)) == 1
    assert len(store.search("QUARTERLY", None, 10, 0)) == 1


def test_index_records_attachments(tmp_path, sample_mbox):
    _, store, _ = _build(tmp_path, sample_mbox)
    rows = store.list_messages("Important", 10, 0)
    atts = store.get_attachments(rows[0]["id"])
    assert atts[0]["filename"] == "invoice.pdf"


def test_index_is_current_false_when_mbox_missing(tmp_path, sample_mbox):
    import os
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    build_index(settings, store)
    assert index_is_current(settings, store) is True
    os.remove(sample_mbox)                          # mbox gone -> must not raise
    assert index_is_current(settings, store) is False


def test_index_is_current_detects_staleness(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    os.utime(sample_mbox, (0, 0))
    assert index_is_current(settings, store) is False


def test_build_index_stamps_schema_version(tmp_path, sample_mbox):
    from mboxviewer.store import SCHEMA_VERSION
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert index_is_current(settings, store) is True


def test_index_is_current_false_on_schema_version_change(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    # mbox unchanged (size+mtime match) but the index was built by an older format
    store.set_meta("schema_version", "1"); store.commit()
    assert index_is_current(settings, store) is False


def test_index_is_current_false_when_schema_version_missing(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    store.conn.execute("DELETE FROM meta WHERE key='schema_version'"); store.commit()
    assert index_is_current(settings, store) is False


def test_rebuild_does_not_duplicate(tmp_path, sample_mbox):
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    assert build_index(settings, store) == 2
    assert build_index(settings, store) == 2  # re-run must not duplicate
    assert dict(store.list_labels())["Inbox"] == 2
    assert len(store.list_messages(None, 100, 0)) == 2


def test_progress_callback_receives_count_and_bytes(tmp_path, sample_mbox, monkeypatch):
    import mboxviewer.indexer as idx
    monkeypatch.setattr(idx, "PROGRESS_EVERY", 1)  # fire on every message
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    calls = []
    idx.build_index(settings, store, progress=lambda c, b: calls.append((c, b)))
    assert len(calls) == 2  # PROGRESS_EVERY=1, sample mbox has 2 messages
    counts = [c for c, _ in calls]
    byts = [b for _, b in calls]
    assert counts == [1, 2]
    assert byts[0] > 0 and byts[0] < byts[1]  # byte progress is positive and monotonic


def test_failed_message_is_not_partially_indexed(tmp_path, sample_mbox, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bad attachment")
    monkeypatch.setattr("mboxviewer.indexer.extract_text", boom)
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path)
    store.create_schema()
    count = build_index(settings, store)
    assert count == 0                                   # both messages failed
    assert store.list_messages(None, 100, 0) == []      # no orphan message rows
    assert store.list_labels() == []                    # no orphan labels


def test_zip_inner_filenames_are_indexed(tmp_path):
    import io, zipfile
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("secret_plans.txt", b"hi")
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(zb.getvalue(), maintype="application", subtype="zip", filename="bundle.zip")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "z.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"))
    store = Store(settings.index_path); store.create_schema(); build_index(settings, store)
    assert len(store.search("secret_plans", None, 10, 0)) == 1   # inner filename searchable


def test_schema_version_bump_marks_v2_index_stale(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    store.set_meta("schema_version", "2"); store.commit()   # built by the previous format
    assert index_is_current(settings, store) is False
