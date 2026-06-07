import threading

from mboxviewer.store import Store


def _store(tmp_path):
    s = Store(str(tmp_path / "i.db"))
    s.create_schema()
    return s


def test_message_and_label_roundtrip(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(0, 100, "<id1>", "Hi", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    lid = s.add_label("Inbox")
    s.link_label(mid, lid)
    s.add_fts(mid, "Hi", "a@x.com", "b@x.com", "hello world", "")
    s.commit()
    assert s.list_labels() == [("Inbox", 1)]
    rows = s.list_messages("Inbox", limit=10, offset=0)
    assert len(rows) == 1 and rows[0]["subject"] == "Hi"


def test_add_label_is_idempotent(tmp_path):
    s = _store(tmp_path)
    assert s.add_label("Work") == s.add_label("Work")


def test_search_matches_body_and_attachment_text(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(0, 100, "<id>", "Invoice", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    s.link_label(mid, s.add_label("Inbox"))
    s.add_fts(mid, "Invoice", "a@x.com", "b@x.com", "see body", "INVOICE 12345")
    s.commit()
    assert [r["id"] for r in s.search("12345", None, 10, 0)] == [mid]
    assert [r["id"] for r in s.search("body", None, 10, 0)] == [mid]
    assert s.search("nomatch", None, 10, 0) == []


def test_search_respects_label_filter(tmp_path):
    s = _store(tmp_path)
    m_inbox = s.add_message(0, 10, "<a>", "Hello", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    s.link_label(m_inbox, s.add_label("Inbox"))
    s.add_fts(m_inbox, "Hello", "a@x.com", "b@x.com", "shared keyword", "")
    m_work = s.add_message(10, 10, "<b>", "Hi", "c@x.com", "b@x.com", "2024-01-02T10:00:00", "raw")
    s.link_label(m_work, s.add_label("Work"))
    s.add_fts(m_work, "Hi", "c@x.com", "b@x.com", "shared keyword", "")
    s.commit()
    assert [r["id"] for r in s.search("keyword", "Inbox", 10, 0)] == [m_inbox]
    assert [r["id"] for r in s.search("keyword", None, 10, 0)] == [m_inbox, m_work] or \
           [r["id"] for r in s.search("keyword", None, 10, 0)] == [m_work, m_inbox]


def test_get_message_and_attachments(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(5, 50, "<id>", "S", "a", "b", "2024-01-01T10:00:00", "raw")
    s.add_attachment(mid, 0, "invoice.pdf", "application/pdf", 999)
    s.commit()
    row = s.get_message_row(mid)
    assert row["offset"] == 5 and row["length"] == 50
    atts = s.get_attachments(mid)
    assert atts[0]["filename"] == "invoice.pdf" and atts[0]["idx"] == 0


def test_all_message_spans(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    spans = s.all_message_spans()
    assert len(spans) == 2 and spans[0]["length"] > 0


def test_attachment_mime_counts_and_files(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["mime"]: r["c"] for r in s.attachment_mime_counts()}
    assert counts["application/pdf"] == 1
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert counts[docx] == 1
    files = s.list_files_by_mimes(["application/pdf"], 10, 0)
    assert len(files) == 1
    assert files[0]["filename"] == "invoice.pdf"
    assert files[0]["subject"] == "Welcome aboard" and files[0]["size"] > 0
    assert s.list_files_by_mimes([], 10, 0) == []


def test_list_files_by_mimes_search(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    pdf = "application/pdf"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    allmimes = [pdf, docx]
    # filename match
    by_name = s.list_files_by_mimes(allmimes, 50, 0, query="invoice")
    assert [f["filename"] for f in by_name] == ["invoice.pdf"]
    # content match: a term inside the indexed PDF text (see conftest sample body/attachment)
    by_content = s.list_files_by_mimes([pdf], 50, 0, query="12345")
    assert any(f["filename"] == "invoice.pdf" for f in by_content)
    # no-mime + query searches across all files
    cross = s.list_files_by_mimes([], 50, 0, query="invoice")
    assert any(f["filename"] == "invoice.pdf" for f in cross)
    # punctuation-only query matches no filename and no FTS rows → empty, no crash
    assert s.list_files_by_mimes(allmimes, 50, 0, query="!!!") == []
    # whitespace-only query is treated as no query → the mime filter still applies
    assert [f["filename"] for f in s.list_files_by_mimes([pdf], 50, 0, query="   ")] == ["invoice.pdf"]
    # no mimes and no query → empty
    assert s.list_files_by_mimes([], 50, 0, query=None) == []


def test_reads_work_from_another_thread(tmp_path):
    s = Store(str(tmp_path / "i.db"))
    s.create_schema()
    mid = s.add_message(0, 10, "<a>", "Hi", "a@x.com", "b@x.com", "2024-01-01T10:00:00", "raw")
    s.commit()
    out = {}

    def reader():
        out["rows"] = s.list_messages(None, 10, 0)
        out["count"] = s.message_count()

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert len(out["rows"]) == 1 and out["rows"][0]["id"] == mid
    assert out["count"] == 1
