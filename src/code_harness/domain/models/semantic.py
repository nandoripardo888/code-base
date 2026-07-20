from dataclasses import dataclass

from code_harness.domain.models.code_location import CodeLocation

Vector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider: str
    provider_version: str
    model_id: str
    dimensions: int
    strategy: str


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    identity: EmbeddingIdentity
    content_hash: str
    vector: Vector
    generated_at: str


@dataclass(frozen=True, slots=True)
class ChunkEmbeddingLink:
    chunk_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    identity: EmbeddingIdentity | None = None
    records: tuple[EmbeddingRecord, ...] = ()
    links: tuple[ChunkEmbeddingLink, ...] = ()
    generated_count: int = 0
    reused_count: int = 0


@dataclass(frozen=True, slots=True)
class EmbeddableChunk:
    chunk_id: str
    location: CodeLocation
    content: str
    content_hash: str
    language: str | None
    file_hash: str


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    chunk: EmbeddableChunk
    score: float


@dataclass(frozen=True, slots=True)
class SemanticPreparationReport:
    ready: bool
    provider: str
    provider_version: str
    model_id: str
    dimensions: int
    strategy: str
    cache_path: str
