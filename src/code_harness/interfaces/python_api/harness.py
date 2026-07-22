from pathlib import Path

from code_harness.application.dto.requests import (
    BuildContextRequest,
    FindDefinitionRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetFileOutlineRequest,
    GetRepositoryMapRequest,
    IndexProjectRequest,
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchCodeRequest,
    SearchFilesRequest,
    SearchRegexRequest,
    SearchTextRequest,
    SemanticSearchRequest,
)
from code_harness.bootstrap.container import ApplicationContainer, build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode
from code_harness.domain.models.code_chunk import SourceRead
from code_harness.domain.models.context import ContextBundle
from code_harness.domain.models.file_listing import FileListingPage
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.index_report import DoctorReport, IndexReport, IndexStatus
from code_harness.domain.models.project import Project
from code_harness.domain.models.repository_map import RepositoryMap
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.semantic import SemanticPreparationReport
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult


class CodeHarness:
    def __init__(self, container: ApplicationContainer) -> None:
        self._container = container

    @classmethod
    def open(cls, root: str | Path) -> "CodeHarness":
        return cls(build_container(Settings.for_root(root)))

    def initialize_index(self) -> ToolResult[Project]:
        return self._container.initialize_index.execute()

    def index_project(
        self, mode: IndexMode | str = IndexMode.INCREMENTAL
    ) -> ToolResult[IndexReport]:
        return self._container.index_project.execute(IndexProjectRequest(IndexMode(mode)))

    def get_index_status(self) -> ToolResult[IndexStatus]:
        return self._container.get_index_status.execute()

    def doctor(self, *, deep: bool = False) -> ToolResult[DoctorReport]:
        return self._container.doctor.execute(deep=deep)

    def prepare_semantic_model(self) -> ToolResult[SemanticPreparationReport]:
        return self._container.prepare_semantic_model.execute()

    def get_file_outline(
        self,
        path: str,
        *,
        include_content: bool | None = None,
        response_format: str = "compact",
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.get_file_outline.execute(
            GetFileOutlineRequest(
                path,
                include_content=include_content,
                response_format=response_format,
            )
        )

    def find_symbol(
        self,
        query: str,
        *,
        max_results: int = 50,
        exact: bool = False,
        include_content: bool | None = None,
        response_format: str = "compact",
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_symbol.execute(
            FindSymbolRequest(
                query,
                max_results,
                exact,
                include_content=include_content,
                response_format=response_format,
            )
        )

    def find_definition(
        self, query: str, *, max_results: int = 20
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_definition.execute(FindDefinitionRequest(query, max_results))

    def find_references(
        self, query: str, *, max_results: int = 100
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_references.execute(FindReferencesRequest(query, max_results))

    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 10_000,
        cursor: str | None = None,
        sort: str = "path",
        sort_direction: str = "asc",
        include_total_count: bool = True,
    ) -> ToolResult[FileListingPage]:
        return self._container.list_files.execute(
            ListFilesRequest(
                include_globs,
                exclude_globs,
                max_results,
                cursor,
                sort,
                sort_direction,
                include_total_count,
            )
        )

    def search_files(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        case_sensitive: bool = False,
    ) -> ToolResult[tuple[FileMatch, ...]]:
        return self._container.search_files.execute(
            SearchFilesRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                case_sensitive,
            )
        )

    def search_text(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.search_text.execute(
            SearchTextRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
        )

    def search_regex(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.search_regex.execute(
            SearchRegexRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
        )

    def semantic_search(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_results: int = 50,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.semantic_search.execute(
            SemanticSearchRequest(
                query,
                include_globs,
                exclude_globs,
                languages,
                max_results,
            )
        )

    def search_code(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 2,
        timeout_seconds: float = 10.0,
    ) -> ToolResult[tuple[HybridSearchHit, ...]]:
        return self._container.search_code.execute(
            SearchCodeRequest(
                query,
                include_globs,
                exclude_globs,
                languages,
                max_results,
                context_lines,
                timeout_seconds,
            )
        )

    def build_context(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_tokens: int = 12_000,
        reserved_tokens: int = 0,
        max_files: int = 12,
        max_snippets: int = 20,
        max_expansion_depth: int = 2,
    ) -> ToolResult[ContextBundle]:
        return self._container.build_context.execute(
            BuildContextRequest(
                query,
                include_globs,
                exclude_globs,
                languages,
                max_tokens,
                reserved_tokens,
                max_files,
                max_snippets,
                max_expansion_depth,
            )
        )

    def get_repository_map(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_files: int = 200,
        max_symbols_per_file: int = 10,
        mode: str = "detailed",
        path: str | None = None,
        max_depth: int | None = None,
        include_symbols: bool | None = None,
    ) -> ToolResult[RepositoryMap]:
        return self._container.get_repository_map.execute(
            GetRepositoryMapRequest(
                include_globs,
                exclude_globs,
                languages,
                max_files,
                max_symbols_per_file,
                mode=mode,
                path=path,
                max_depth=max_depth,
                include_symbols=include_symbols,
            )
        )

    def read_file(
        self,
        path: str,
        *,
        max_chars: int = 200_000,
        max_lines: int = 5_000,
        include_line_numbers: bool = False,
    ) -> ToolResult[SourceRead]:
        return self._container.read_file.execute(
            ReadFileRequest(path, max_chars, max_lines, include_line_numbers)
        )

    def read_range(
        self,
        path: str,
        start_line: int,
        end_line: int,
        *,
        max_chars: int = 200_000,
        include_line_numbers: bool = False,
    ) -> ToolResult[SourceRead]:
        return self._container.read_range.execute(
            ReadRangeRequest(path, start_line, end_line, max_chars, include_line_numbers)
        )
