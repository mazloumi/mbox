import pytest

from mboxviewer.store import Store


@pytest.fixture
def vstore(tmp_path):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    s.ensure_vector_schema(3)
    return s


def _msg(s):
    return s.add_message(0, 1, "<m>", "Subj", "a@x.com", "b@x.com", "2024-01-01", "raw")


def test_chunk_roundtrip_and_pending_vectors(vstore):
    mid = _msg(vstore)
    c1 = vstore.add_chunk(mid, "body", 0, None, "hello")
    c2 = vstore.add_chunk(mid, "attachment", 0, 2, "world")
    assert vstore.count_chunks() == 2
    pending = vstore.chunks_without_vectors(10)
    assert {cid for cid, _ in pending} == {c1, c2}


def test_knn_orders_by_distance(vstore):
    mid = _msg(vstore)
    a = vstore.add_chunk(mid, "body", 0, None, "a")
    b = vstore.add_chunk(mid, "body", 1, None, "b")
    vstore.add_vector(a, [1.0, 0.0, 0.0])
    vstore.add_vector(b, [0.0, 1.0, 0.0])
    hits = vstore.knn_search([0.9, 0.1, 0.0], 2)
    assert [h[0] for h in hits] == [a, b]          # chunk_id order
    assert all(h[1] == mid for h in hits)          # message_id mapped
    assert vstore.chunks_without_vectors(10) == [] # both embedded now


def test_clear_vectors_keeps_chunk_text(vstore):
    mid = _msg(vstore)
    c = vstore.add_chunk(mid, "body", 0, None, "keep me")
    vstore.add_vector(c, [1.0, 2.0, 3.0])
    vstore.clear_vectors()
    assert vstore.count_chunks() == 1                       # text preserved
    assert vstore.chunks_without_vectors(10) == [(c, "keep me")]


def test_knn_search_graceful_when_vectors_dropped(vstore):
    vstore.clear_vectors()  # drops vec_chunks
    assert vstore.knn_search([0.0, 0.0, 0.0], 5) == []


def test_embed_meta_roundtrip(vstore):
    vstore.embed_meta_set("BAAI/bge-small-en-v1.5", 384, "local")
    assert vstore.embed_meta_get() == ("BAAI/bge-small-en-v1.5", 384, "local")


def test_search_fts_messages(vstore):
    mid = _msg(vstore)
    vstore.add_fts(mid, "Subj", "a@x.com", "b@x.com", "the quick brown fox", "")
    assert vstore.search_fts_messages("quick", 5) == [mid]
    assert vstore.search_fts_messages("zzz", 5) == []


def test_request_path_queries_work_under_vector_driver(vstore):
    mid = _msg(vstore)
    vstore.add_fts(mid, "Subj", "a@x.com", "b@x.com", "hello world", "")
    # search() and list_files_by_category() must run (and gracefully handle) under
    # the enable_vectors=True driver (pysqlite3 on dev Python) without raising.
    assert any(r["id"] == mid for r in vstore.search("hello", None, 10, 0))
    assert vstore.list_files_by_category(None, 10, 0, query="nope") == []
