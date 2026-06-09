import os
import pytest

from mboxviewer import embed
from mboxviewer.config import Settings


def test_factory_local_by_default():
    s = Settings(mbox_path="x", index_path="y")
    e = embed.make_embedder(s)
    assert isinstance(e, embed.LocalEmbedder)
    assert e.model_name == "BAAI/bge-small-en-v1.5"


def test_factory_ollama():
    s = Settings(mbox_path="x", index_path="y", embed_backend="ollama",
                 embed_model="nomic-embed-text", ollama_url="http://h:1")
    e = embed.make_embedder(s)
    assert isinstance(e, embed.OllamaEmbedder)
    assert e.model_name == "nomic-embed-text"


def test_factory_rejects_unknown_backend():
    s = Settings(mbox_path="x", index_path="y", embed_backend="bogus")
    with pytest.raises(ValueError):
        embed.make_embedder(s)


@pytest.mark.skipif(os.environ.get("MBOX_TEST_FASTEMBED") != "1",
                    reason="set MBOX_TEST_FASTEMBED=1 to run the real fastembed download")
def test_local_embedder_real():
    e = embed.LocalEmbedder("BAAI/bge-small-en-v1.5")
    vecs = e.embed_texts(["hello world", "goodbye"])
    assert len(vecs) == 2
    assert e.dim == len(vecs[0]) == 384
