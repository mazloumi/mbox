from mboxviewer.status import EmbedStatus


def test_lifecycle():
    s = EmbedStatus()
    assert s.snapshot()["state"] == "idle"
    assert s.snapshot()["ready"] is False

    s.start_chunking(message_total=10)
    s.update_chunks(messages_done=5)
    snap = s.snapshot()
    assert snap["state"] == "chunking"
    assert snap["messages_done"] == 5
    assert snap["messages_total"] == 10

    s.start_embedding(vectors_total=40)
    s.update_vectors(vectors_done=10)
    snap = s.snapshot()
    assert snap["state"] == "embedding"
    assert snap["vectors_done"] == 10
    assert snap["vectors_total"] == 40
    assert snap["ready"] is False

    s.finish()
    snap = s.snapshot()
    assert snap["state"] == "ready"
    assert snap["ready"] is True


def test_fail_does_not_stick_ready():
    s = EmbedStatus()
    s.start_chunking(1)
    s.fail("boom")
    snap = s.snapshot()
    assert snap["state"] == "error"
    assert snap["ready"] is False
    assert snap["error"] == "boom"
