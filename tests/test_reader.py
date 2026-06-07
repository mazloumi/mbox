from mboxviewer.reader import (
    iter_message_spans, read_message, iter_attachments, get_display_body, parse_labels,
)


def test_spans_find_two_messages(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    assert len(spans) == 2
    for offset, length in spans:
        assert length > 0


def test_read_message_parses_headers(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    assert msg["subject"] == "Welcome aboard"
    assert msg["from"] == "alice@example.com"
    assert parse_labels(msg["x-gmail-labels"]) == ["Inbox", "Important"]


def test_iter_attachments_returns_payload(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    atts = list(iter_attachments(msg))
    assert len(atts) == 1
    idx, filename, mime, payload = atts[0]
    assert idx == 0 and filename == "invoice.pdf"
    assert mime == "application/pdf" and payload[:4] == b"%PDF"


def test_display_body_prefers_html(sample_mbox):
    spans = list(iter_message_spans(sample_mbox))
    msg = read_message(sample_mbox, *spans[0])
    mime, content = get_display_body(msg)
    assert mime == "text/html" and "<b>Bob</b>" in content


def test_read_message_unescapes_mboxrd_from(tmp_path):
    raw = (
        b"From - Mon Jan 01 10:00:00 2024\n"
        b"Subject: t\n"
        b"\n"
        b">From the savings\n"
        b">>From nested\n"
    )
    p = tmp_path / "m.mbox"
    p.write_bytes(raw)
    spans = list(iter_message_spans(str(p)))
    msg = read_message(str(p), *spans[0])
    body = msg.get_content()
    assert "From the savings" in body and ">From the savings" not in body
    assert ">From nested" in body
