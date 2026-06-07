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
