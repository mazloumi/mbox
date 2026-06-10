import pytest

from mboxviewer.store import Store
from mboxviewer import retrieve


class FakeEmbedder:
    """Deterministic 3-d vectors so KNN order is predictable in tests."""
    model_name = "fake"
    dim = 3

    def __init__(self, mapping):
        self._mapping = mapping  # text-substring -> vector

    def embed_texts(self, texts):
        out = []
        for t in texts:
            for key, vec in self._mapping.items():
                if key in t:
                    out.append(vec)
                    break
            else:
                out.append([0.0, 0.0, 0.0])
        return out


@pytest.fixture
def seeded(tmp_path):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    s.ensure_vector_schema(3)
    rows = {}
    for mid_text, vec, subj, body in [
        ("apple", [1, 0, 0], "Apple thread", "about apples and orchards"),
        ("banana", [0, 1, 0], "Banana thread", "yellow banana split"),
        ("cherry", [0, 0, 1], "Cherry thread", "cherry pie recipe"),
    ]:
        mid = s.add_message(0, 1, "<m>", subj, "a@x.com", "b@x.com", "2024-01-01", "raw")
        s.add_fts(mid, subj, "a@x.com", "b@x.com", body, "")
        cid = s.add_chunk(mid, "body", 0, None, f"{subj}\n{body}")
        s.add_vector(cid, [float(v) for v in vec])
        rows[mid_text] = mid
    return s, rows


def test_search_ranks_semantic_match_first(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"banana-query": [0.0, 1.0, 0.0]})
    results = retrieve.search(store, emb, "banana-query", limit=3)
    assert results[0]["id"] == rows["banana"]


def test_retrieve_context_returns_snippets_with_ids(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"cherry-query": [0.0, 0.0, 1.0]})
    snips = retrieve.retrieve_context(store, emb, "cherry-query", budget_chars=10000)
    assert snips[0].message_id == rows["cherry"]
    assert "cherry" in snips[0].text.lower()
    assert snips[0].subject == "Cherry thread"


def test_budget_caps_snippets(seeded):
    store, rows = seeded
    emb = FakeEmbedder({"q": [1.0, 1.0, 1.0]})
    snips = retrieve.retrieve_context(store, emb, "apple banana cherry", budget_chars=50)
    total = sum(len(s.text) for s in snips)
    assert total <= 50 + max(len(s.text) for s in snips)  # cap + one snippet's slack
    assert len(snips) >= 1


def test_retrieve_context_fts_only_uses_preview_fallback(tmp_path):
    s = Store(str(tmp_path / "i.db"), enable_vectors=True)
    s.create_schema()
    s.ensure_vector_schema(3)
    # Message seeded into FTS only (no chunk, no vector) -> chunk_id is None path.
    mid = s.add_message(0, 1, "<m>", "Durian thread", "a@x.com", "b@x.com",
                        "2024-01-01", "raw", preview="some preview text about durian")
    s.add_fts(mid, "Durian thread", "a@x.com", "b@x.com", "spiky durian fruit", "")
    emb = FakeEmbedder({})  # query embeds to zero vector; no KNN hits
    snips = retrieve.retrieve_context(s, emb, "durian", budget_chars=10000)
    assert snips[0].message_id == mid
    assert snips[0].text == "some preview text about durian"
    assert snips[0].subject == "Durian thread"
