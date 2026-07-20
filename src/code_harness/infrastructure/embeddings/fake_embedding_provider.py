from collections.abc import Mapping, Sequence
from hashlib import sha256

from code_harness.domain.models.semantic import EmbeddingIdentity, Vector
from code_harness.infrastructure.embeddings.fastembed_provider import _normalize


class FakeEmbeddingProvider:
    def __init__(
        self,
        vectors: Mapping[str, Vector] | None = None,
        *,
        dimensions: int = 4,
        model_id: str = "fake-model",
    ) -> None:
        self._vectors = dict(vectors or {})
        self._identity = EmbeddingIdentity(
            "fake",
            "1",
            model_id,
            dimensions,
            "windowed-mean-l2-v1:1500:150",
        )
        self.document_calls: list[tuple[str, ...]] = []
        self.query_calls: list[str] = []

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._identity

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        self.document_calls.append(tuple(texts))
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> Vector:
        self.query_calls.append(text)
        return self._vector(text)

    def _vector(self, text: str) -> Vector:
        configured = self._vectors.get(text)
        if configured is not None:
            if len(configured) != self._identity.dimensions:
                raise ValueError("configured fake vector has the wrong dimensions")
            return _normalize(configured)
        digest = sha256(text.encode("utf-8")).digest()
        values = tuple(
            (digest[index] - 127.5) / 127.5 for index in range(self._identity.dimensions)
        )
        return _normalize(values)
