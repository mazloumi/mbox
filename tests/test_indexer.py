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


def test_index_is_current_detects_staleness(tmp_path, sample_mbox):
    settings, store, _ = _build(tmp_path, sample_mbox)
    assert index_is_current(settings, store) is True
    os.utime(sample_mbox, (0, 0))
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
    assert len(calls) >= 1
    last_count, last_bytes = calls[-1]
    assert last_count >= 1 and last_bytes > 0


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
