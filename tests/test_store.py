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
    s.add_attachment(mid, 0, "invoice.pdf", "application/pdf", 999, "Documents")
    s.commit()
    row = s.get_message_row(mid)
    assert row["offset"] == 5 and row["length"] == 50
    atts = s.get_attachments(mid)
    assert atts[0]["filename"] == "invoice.pdf" and atts[0]["idx"] == 0


def test_query_attachments_catalog(tmp_path):
    s = _store(tmp_path)
    m1 = s.add_message(0, 10, "<a>", "Trip", "ava@x.com", "me@x.com", "2024-03-01T00:00:00", "r")
    m2 = s.add_message(10, 10, "<b>", "Work", "boss@x.com", "me@x.com", "2024-01-01T00:00:00", "r")
    s.add_attachment(m1, 0, "beach.mp4", "video/mp4", 100, "Media")
    s.add_attachment(m1, 1, "song.m4a", "audio/mp4", 50, "Media")
    s.add_attachment(m2, 0, "report.pdf", "application/pdf", 80, "Documents")
    s.commit()

    total, rows = s.query_attachments(category="Media")
    assert total == 2
    assert {r["filename"] for r in rows} == {"beach.mp4", "song.m4a"}
    # joined message metadata is present for citation
    assert rows[0]["message_id"] == m1 and rows[0]["from_addr"] == "ava@x.com"

    # filename + sender filters
    assert s.query_attachments(filename_contains="beach")[0] == 1
    assert s.query_attachments(category="Documents", sender_contains="boss")[0] == 1
    assert s.query_attachments(category="Documents", sender_contains="ava")[0] == 0

    # limit caps the sample but not the reported total
    total_all, sample = s.query_attachments(limit=1)
    assert total_all == 3 and len(sample) == 1

    # attachment_kinds returns every match (mime, filename) for an exact breakdown
    kinds = s.attachment_kinds(category="Media")
    assert len(kinds) == 2
    assert {(k["mime"], k["filename"]) for k in kinds} == {
        ("video/mp4", "beach.mp4"), ("audio/mp4", "song.m4a")}


def test_all_message_spans(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    spans = s.all_message_spans()
    assert len(spans) == 2 and spans[0]["length"] > 0


def test_category_counts_and_listing(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["category"]: r["c"] for r in s.attachment_category_counts()}
    assert counts.get("Documents") == 2          # invoice.pdf + report.docx
    files = s.list_files_by_category("Documents", 10, 0)
    assert sorted(f["filename"] for f in files) == ["invoice.pdf", "report.docx"]
    assert s.list_files_by_category("Documents", 10, 0, query="invoice")[0]["filename"] == "invoice.pdf"
    assert s.list_files_by_category(None, 10, 0) == []          # no category, no query
    assert s.list_files_by_category("Nope", 10, 0) == []        # unknown category


def test_octet_stream_pdf_categorized_by_extension(tmp_path):
    import io
    from email.message import EmailMessage
    from email.generator import BytesGenerator
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    m = EmailMessage()
    m["Subject"] = "x"; m["From"] = "a@x.com"; m["To"] = "b@x.com"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"; m["X-Gmail-Labels"] = "Inbox"
    m.set_content("body")
    m.add_attachment(b"%PDF-1.4 junk", maintype="application", subtype="octet-stream",
                     filename="scan.pdf")
    buf = io.BytesIO(); BytesGenerator(buf).flatten(m); data = buf.getvalue()
    p = tmp_path / "o.mbox"
    p.write_bytes(b"From - x\n" + data + (b"" if data.endswith(b"\n") else b"\n") + b"\n")
    settings = Settings(mbox_path=str(p), index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    counts = {r["category"]: r["c"] for r in s.attachment_category_counts()}
    assert counts.get("Documents") == 1          # octet-stream + .pdf -> Documents


def test_category_column_migration(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    # Simulate a pre-existing attachments table WITHOUT the category column.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE attachments (id INTEGER PRIMARY KEY, message_id INTEGER, idx INTEGER,"
        " filename TEXT, mime TEXT, size INTEGER);"
        "INSERT INTO attachments(message_id, idx, filename, mime, size) VALUES(1,0,'a.pdf','x',1);")
    conn.commit(); conn.close()
    s = Store(db)
    s.create_schema()    # must ALTER-add category without data loss
    s.create_schema()    # idempotent
    assert s.conn.execute("SELECT category FROM attachments").fetchone()[0] is None


def test_list_files_by_category_search(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    # filename match within category
    by_name = s.list_files_by_category("Documents", 50, 0, query="invoice")
    assert [f["filename"] for f in by_name] == ["invoice.pdf"]
    # content match: a term inside the indexed PDF text (see conftest sample body/attachment)
    by_content = s.list_files_by_category("Documents", 50, 0, query="12345")
    assert any(f["filename"] == "invoice.pdf" for f in by_content)
    # no category + query searches across all files
    cross = s.list_files_by_category(None, 50, 0, query="invoice")
    assert any(f["filename"] == "invoice.pdf" for f in cross)
    # punctuation-only query matches no filename and no FTS rows → empty, no crash
    assert s.list_files_by_category("Documents", 50, 0, query="!!!") == []
    # whitespace-only query is treated as no query → the category filter still applies
    assert [f["filename"] for f in s.list_files_by_category("Documents", 50, 0, query="   ")] == ["invoice.pdf", "report.docx"]
    # no category and no query → empty
    assert s.list_files_by_category(None, 50, 0, query=None) == []


def test_preview_stored_and_returned(tmp_path):
    s = _store(tmp_path)
    mid = s.add_message(0, 100, "<id>", "Hi", "a@x.com", "b@x.com",
                        "2024-01-01T10:00:00", "raw", preview="hello there world")
    s.commit()
    assert s.get_message_row(mid)["preview"] == "hello there world"
    assert s.list_messages(None, 10, 0)[0]["preview"] == "hello there world"


def _seed_filter_messages(s):
    a = s.add_message(0, 10, "<a>", "Alpha", "alice@example.com", "b@x.com",
                      "2024-01-01T10:00:00", "raw", preview="pa")
    b = s.add_message(10, 10, "<b>", "Beta", "carol@example.com", "b@x.com",
                      "2024-02-01T10:00:00", "raw", preview="pb")
    c = s.add_message(20, 10, "<c>", "Gamma", "carol@example.com", "b@x.com",
                      "2024-03-01T10:00:00", "raw", preview="pc")
    s.add_attachment(c, 0, "f.pdf", "application/pdf", 1, "Documents")
    s.commit()
    return a, b, c


def test_list_messages_from_q_filter(tmp_path):
    s = _store(tmp_path)
    a, b, c = _seed_filter_messages(s)
    ids = {r["id"] for r in s.list_messages(None, 10, 0, from_q="carol")}
    assert ids == {b, c}


def test_list_messages_has_attachment_filter(tmp_path):
    s = _store(tmp_path)
    a, b, c = _seed_filter_messages(s)
    ids = [r["id"] for r in s.list_messages(None, 10, 0, has_attachment=True)]
    assert ids == [c]


def test_list_messages_sort_asc(tmp_path):
    s = _store(tmp_path)
    a, b, c = _seed_filter_messages(s)
    desc = [r["id"] for r in s.list_messages(None, 10, 0)]
    asc = [r["id"] for r in s.list_messages(None, 10, 0, sort="date_asc")]
    assert desc == [c, b, a]
    assert asc == [a, b, c]


def test_list_messages_date_range_filter(tmp_path):
    s = _store(tmp_path)
    a, b, c = _seed_filter_messages(s)
    ids = {r["id"] for r in s.list_messages(None, 10, 0,
                                            date_from="2024-02-01", date_to="2024-02-01")}
    assert ids == {b}


def test_search_with_filters(tmp_path):
    s = _store(tmp_path)
    a, b, c = _seed_filter_messages(s)
    for mid, frm in ((a, "alice@example.com"), (b, "carol@example.com"), (c, "carol@example.com")):
        s.add_fts(mid, "subj", frm, "b@x.com", "shared keyword body", "")
    s.commit()
    ids = {r["id"] for r in s.search("keyword", None, 10, 0, from_q="carol")}
    assert ids == {b, c}
    ids2 = {r["id"] for r in s.search("keyword", None, 10, 0, has_attachment=True)}
    assert ids2 == {c}
    asc = [r["id"] for r in s.search("keyword", None, 10, 0, sort="date_asc")]
    assert asc == [a, b, c]


def test_list_files_for_export_shape_and_cap(tmp_path, sample_mbox):
    from mboxviewer.config import Settings
    from mboxviewer.indexer import build_index
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    s = Store(settings.index_path); s.create_schema(); build_index(settings, s)
    rows = s.list_files_for_export("Documents", None, 100)
    assert sorted(r["filename"] for r in rows) == ["invoice.pdf", "report.docx"]
    r0 = rows[0]
    assert set(r0.keys()) >= {"message_id", "idx", "filename", "mime", "size"}
    capped = s.list_files_for_export("Documents", None, 1)
    assert len(capped) == 1


def test_integrity_reads_meta(tmp_path):
    s = _store(tmp_path)
    s.set_meta("indexed_count", "5")
    s.set_meta("skipped_count", "2")
    s.set_meta("skipped_sample", '[{"offset": 7, "reason": "boom"}]')
    s.commit()
    rep = s.integrity()
    assert rep == {"indexed": 5, "skipped": 2, "sample": [{"offset": 7, "reason": "boom"}]}


def test_integrity_defaults_when_unset(tmp_path):
    s = _store(tmp_path)
    assert s.integrity() == {"indexed": 0, "skipped": 0, "sample": []}


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
