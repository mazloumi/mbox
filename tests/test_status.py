from mboxviewer.status import IndexStatus


def test_new_status_is_idle():
    s = IndexStatus().snapshot()
    assert s["indexing"] is False and s["ready"] is False
    assert s["messages"] == 0 and s["percent"] == 0.0 and s["error"] is None


def test_start_and_update_progress():
    st = IndexStatus()
    st.start(1000)
    st.update(250, 400)
    s = st.snapshot()
    assert s["indexing"] is True and s["ready"] is False
    assert s["messages"] == 250 and s["bytes_done"] == 400
    assert s["bytes_total"] == 1000 and s["percent"] == 40.0


def test_finish_sets_ready_and_full():
    st = IndexStatus()
    st.start(1000)
    st.update(250, 400)
    st.finish()
    s = st.snapshot()
    assert s["indexing"] is False and s["ready"] is True
    assert s["bytes_done"] == 1000 and s["percent"] == 100.0


def test_fail_records_error():
    st = IndexStatus()
    st.start(1000)
    st.fail(RuntimeError("boom"))
    s = st.snapshot()
    assert s["indexing"] is False and s["ready"] is False
    assert "boom" in s["error"]


def test_mark_ready_for_reused_index():
    st = IndexStatus()
    st.mark_ready(messages=42)
    s = st.snapshot()
    assert s["ready"] is True and s["indexing"] is False
    assert s["messages"] == 42 and s["percent"] == 100.0
