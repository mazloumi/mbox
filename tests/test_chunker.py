from mboxviewer.chunker import iter_chunks, Chunk


def test_short_body_single_chunk_with_header():
    chunks = list(iter_chunks(subject="Hi", from_addr="a@x.com", date="2024-01-01",
                              body="hello world", attachments=[]))
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert c.kind == "body"
    assert c.ord == 0
    assert c.source_idx is None
    assert c.text.startswith("Hi · a@x.com · 2024-01-01\n")
    assert "hello world" in c.text


def test_long_body_windows_with_overlap():
    body = "x" * 4500  # > 2 windows at 2000/200
    chunks = [c for c in iter_chunks("S", "f", "d", body, []) if c.kind == "body"]
    assert len(chunks) == 3            # 0..2000, 1800..3800, 3600..4500
    assert [c.ord for c in chunks] == [0, 1, 2]
    # overlap: window 1 starts 200 chars before window 0 ends
    assert chunks[1].text.count("x") == 2000


def test_attachments_chunked_and_capped():
    big = "y" * 100000  # would be ~53 windows; cap to 20
    atts = [(3, "report.pdf", big)]   # (idx, filename, extracted_text)
    chunks = [c for c in iter_chunks("S", "f", "d", "", atts) if c.kind == "attachment"]
    assert len(chunks) == 20
    assert all(c.source_idx == 3 for c in chunks)
    assert chunks[0].text.startswith("S · f · d · report.pdf\n")


def test_empty_text_skipped():
    atts = [(0, "blank.txt", "   "), (1, "ok.txt", "real content")]
    kinds = [(c.kind, c.source_idx) for c in iter_chunks("S", "f", "d", "", atts)]
    assert ("attachment", 0) not in kinds   # whitespace-only attachment produces no chunk
    assert ("attachment", 1) in kinds
