from mboxviewer.config import Settings
from mboxviewer.store import Store
from mboxviewer.status import EmbedStatus
from mboxviewer.indexer import build_index
from mboxviewer import embed_index


class FakeEmbedder:
    model_name = "fake"
    dim = 4

    def embed_texts(self, texts):
        out = []
        for t in texts:
            h = sum(ord(ch) for ch in t)
            out.append([float(h % 7), float(len(t) % 5), 1.0, 0.0])
        return out


def _indexed_store(tmp_path, sample_mbox):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    settings = Settings(mbox_path=sample_mbox, index_path=str(tmp_path / "i.db"))
    build_index(settings, s)
    return settings, s


def test_chunk_then_embed(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    emb = FakeEmbedder()
    status = EmbedStatus()

    embed_index.build_chunks(settings, store, status)
    assert store.count_chunks() > 0
    kinds = {r["kind"] for r in store.conn.execute("SELECT kind FROM chunks")}
    assert "body" in kinds and "attachment" in kinds

    embed_index.build_embeddings(settings, store, emb, status)
    assert store.chunks_without_vectors(100) == []
    assert status.snapshot()["ready"] is True
    assert store.embed_meta_get() == ("fake", 4, "local")


def test_chunk_pass_is_resumable(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    embed_index.build_chunks(settings, store, EmbedStatus())
    n = store.count_chunks()
    embed_index.build_chunks(settings, store, EmbedStatus())  # re-run is a no-op
    assert store.count_chunks() == n


def test_bad_message_is_skipped_without_partial_chunks(tmp_path, sample_mbox, monkeypatch):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    import mboxviewer.embed_index as ei
    real = ei.extract_text
    def boom(filename, mime, payload):
        if filename == "report.docx":
            raise RuntimeError("synthetic extract failure")
        return real(filename, mime, payload)
    monkeypatch.setattr(ei, "extract_text", boom)
    ei.build_chunks(settings, store, EmbedStatus())
    # the other message (invoice.pdf) still produced chunks; the failed one rolled back fully
    assert store.count_chunks() > 0
    msg_ids = {r["message_id"] for r in store.conn.execute("SELECT DISTINCT message_id FROM chunks")}
    # exactly one message contributed chunks (the failed message has none)
    assert len(msg_ids) == 1


def test_embed_pass_resumes_after_partial(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    emb = FakeEmbedder()
    embed_index.build_chunks(settings, store, EmbedStatus())
    embed_index.build_embeddings(settings, store, emb, EmbedStatus())
    store.clear_vectors()
    store.ensure_vector_schema(emb.dim)
    assert len(store.chunks_without_vectors(100)) == store.count_chunks()
    embed_index.build_embeddings(settings, store, emb, EmbedStatus())
    assert store.chunks_without_vectors(100) == []


def test_embed_rebuilds_on_model_change(tmp_path, sample_mbox):
    settings, store = _indexed_store(tmp_path, sample_mbox)
    embed_index.build_chunks(settings, store, EmbedStatus())
    embed_index.build_embeddings(settings, store, FakeEmbedder(), EmbedStatus())
    assert store.embed_meta_get() == ("fake", 4, "local")
    assert store.chunks_without_vectors(100) == []

    class FakeEmbedder6:
        model_name = "fake6"
        dim = 6
        def embed_texts(self, texts):
            return [[1.0] * 6 for _ in texts]

    # Switching to a different model/dim must rebuild the vectors at the new dim.
    embed_index.build_embeddings(settings, store, FakeEmbedder6(), EmbedStatus())
    assert store.embed_meta_get() == ("fake6", 6, "local")
    assert store.chunks_without_vectors(100) == []
    assert isinstance(store.knn_search([1.0] * 6, 1), list)  # works at the new dim
