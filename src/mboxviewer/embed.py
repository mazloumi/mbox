"""Embedding backends behind one interface. Heavy deps are imported lazily so a
deployment with the semantic tier off never loads them."""
from typing import List


class LocalEmbedder:
    """fastembed (ONNX, CPU). The model loads on first embed and is cached."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None
        self._dim = None

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding  # lazy
            self._model = TextEmbedding(model_name=self.model_name)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        self._ensure()
        return [list(map(float, v)) for v in self._model.embed(list(texts))]

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_texts(["dimension probe"])[0])
        return self._dim


class OllamaEmbedder:
    """Host-side Ollama via HTTP (Metal-accelerated). Optional backend."""

    def __init__(self, model_name: str, url: str):
        self.model_name = model_name
        self.url = url.rstrip("/")
        self._dim = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        import httpx  # lazy
        out = []
        with httpx.Client(timeout=120) as client:
            for t in texts:
                r = client.post(f"{self.url}/api/embeddings",
                                json={"model": self.model_name, "prompt": t})
                r.raise_for_status()
                out.append([float(x) for x in r.json()["embedding"]])
        return out

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_texts(["dimension probe"])[0])
        return self._dim


def make_embedder(settings):
    backend = settings.embed_backend
    if backend == "local":
        return LocalEmbedder(settings.embed_model)
    if backend == "ollama":
        return OllamaEmbedder(settings.embed_model, settings.ollama_url)
    raise ValueError(f"unknown EMBED_BACKEND: {backend!r}")
