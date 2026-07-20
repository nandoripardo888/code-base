from typing import Protocol

from code_harness.domain.enums import IndexMode
from code_harness.domain.models.index_report import (
    FileIndexUpdate,
    FtsCandidate,
    IndexReport,
    IndexStatus,
    StoredFile,
)
from code_harness.domain.models.project import Project
from code_harness.domain.models.semantic import EmbeddingBatch
from code_harness.domain.models.structural import StructuralSearchResult


class RepositoryStore(Protocol):
    def initialize(self, project: Project) -> None: ...

    def list_files(self, project_id: str) -> tuple[StoredFile, ...]: ...

    def start_run(self, project_id: str, mode: IndexMode, started_at: str) -> int: ...

    def commit_files(
        self,
        report: IndexReport,
        updates: tuple[FileIndexUpdate, ...],
        removed_paths: tuple[str, ...],
    ) -> None: ...

    def commit_embeddings(self, embeddings: EmbeddingBatch) -> None: ...

    def complete_run(self, run_id: int, report: IndexReport) -> None: ...

    def fail_run(self, run_id: int, finished_at: str, message: str) -> None: ...

    def get_status(self, project: Project) -> IndexStatus: ...

    def search_fts(
        self,
        project_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[FtsCandidate, ...]: ...

    def get_outline(self, project_id: str, path: str) -> tuple[StructuralSearchResult, ...]: ...

    def find_symbols(
        self,
        project_id: str,
        query: str,
        *,
        exact: bool,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]: ...

    def find_symbols_by_ids(
        self,
        project_id: str,
        symbol_ids: tuple[str, ...],
    ) -> tuple[StructuralSearchResult, ...]: ...

    def list_symbols(
        self,
        project_id: str,
        paths: tuple[str, ...],
        *,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]: ...

    def find_references(
        self,
        project_id: str,
        target_name: str,
        *,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]: ...
