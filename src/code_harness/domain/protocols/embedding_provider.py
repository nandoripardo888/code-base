from collections.abc import Sequence
from typing import Protocol

from code_harness.domain.models.semantic import EmbeddingIdentity, Vector


class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> EmbeddingIdentity: ...

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...

    def embed_query(self, text: str) -> Vector: ...
