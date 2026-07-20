from dataclasses import dataclass

from code_harness.domain.enums import DiagnosticStatus, IndexMode, IndexState
from code_harness.domain.models.structural import AnalyzeResult


@dataclass(frozen=True, slots=True)
class StoredFile:
    path: str
    size_bytes: int
    modified_at_ns: int
    language: str | None
    encoding: str
    content_hash: str
    indexed_at: str
    parser_name: str | None = None
    parser_version: str | None = None
    parse_state: str | None = None
    chunking_version: str | None = None


@dataclass(frozen=True, slots=True)
class IndexedSource:
    path: str
    content: str
    size_bytes: int
    modified_at_ns: int
    language: str | None
    encoding: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class FileIndexUpdate:
    source: IndexedSource
    update_content: bool = True
    analysis: AnalyzeResult | None = None
    chunking_version: str | None = None


@dataclass(frozen=True, slots=True)
class IndexReport:
    project_id: str
    mode: IndexMode
    state: IndexState
    discovered_files: int
    new_files: int
    changed_files: int
    removed_files: int
    unchanged_files: int
    indexed_files: int
    warning_files: int
    started_at: str
    finished_at: str
    warnings: tuple[str, ...] = ()
    indexed_symbols: int = 0
    indexed_references: int = 0
    indexed_chunks: int = 0
    parser_failures: int = 0
    generated_embeddings: int = 0
    reused_embeddings: int = 0
    embedded_chunks: int = 0
    embedding_failures: int = 0


@dataclass(frozen=True, slots=True)
class IndexRunSummary:
    mode: IndexMode
    state: IndexState
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    discovered_files: int
    indexed_files: int
    unchanged_files: int
    warning_files: int
    generated_embeddings: int = 0
    reused_embeddings: int = 0


@dataclass(frozen=True, slots=True)
class IndexStatus:
    project_id: str
    state: IndexState
    schema_version: int
    file_count: int
    fts_document_count: int
    warning_files: int
    symbol_count: int = 0
    reference_count: int = 0
    chunk_count: int = 0
    parser_failure_count: int = 0
    structural_schema_ready: bool = False
    semantic_schema_ready: bool = False
    embedding_count: int = 0
    embedded_chunk_count: int = 0
    semantic_model_id: str | None = None
    last_run: IndexRunSummary | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FtsCandidate:
    path: str
    rank: float


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    status: DiagnosticStatus
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    healthy: bool
    checks: tuple[DiagnosticCheck, ...]
