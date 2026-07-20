from collections.abc import Sequence
from typing import Protocol

from code_harness.domain.models.semantic import (
    EmbeddableChunk,
    EmbeddingIdentity,
    EmbeddingRecord,
    Vector,
    VectorSearchHit,
)


class VectorIndex(Protocol):
    def get_cached_embeddings(
        self,
        identity: EmbeddingIdentity,
        content_hashes: Sequence[str],
    ) -> tuple[EmbeddingRecord, ...]: ...

    def list_unembedded_chunks(
        self,
        project_id: str,
        identity: EmbeddingIdentity,
    ) -> tuple[EmbeddableChunk, ...]: ...

    def search_vectors(
        self,
        project_id: str,
        identity: EmbeddingIdentity,
        query_vector: Vector,
        *,
        limit: int,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
    ) -> tuple[VectorSearchHit, ...]: ...
