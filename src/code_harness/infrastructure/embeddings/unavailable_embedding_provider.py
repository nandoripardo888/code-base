from collections.abc import Sequence

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import EmbeddingIdentity, Vector


class UnavailableEmbeddingProvider:
    def __init__(self, message: str) -> None:
        self._message = message

    @property
    def identity(self) -> EmbeddingIdentity:
        raise EmbeddingUnavailableError(self._message)

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        raise EmbeddingUnavailableError(self._message)

    def embed_query(self, text: str) -> Vector:
        raise EmbeddingUnavailableError(self._message)
