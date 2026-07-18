from pathlib import Path

from code_harness.application.dto.requests import (
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchFilesRequest,
    SearchRegexRequest,
    SearchTextRequest,
)
from code_harness.bootstrap.container import ApplicationContainer, build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.tool_result import ToolResult


class CodeHarness:
    def __init__(self, container: ApplicationContainer) -> None:
        self._container = container

    @classmethod
    def open(cls, root: str | Path) -> "CodeHarness":
        return cls(build_container(Settings.for_root(root)))

    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 10_000,
    ) -> ToolResult[tuple[SourceFile, ...]]:
        return self._container.list_files.execute(
            ListFilesRequest(include_globs, exclude_globs, max_results)
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

    def read_file(
        self,
        path: str,
        *,
        max_chars: int = 200_000,
        max_lines: int = 5_000,
    ) -> ToolResult[CodeSnippet]:
        return self._container.read_file.execute(ReadFileRequest(path, max_chars, max_lines))

    def read_range(
        self,
        path: str,
        start_line: int,
        end_line: int,
        *,
        max_chars: int = 200_000,
    ) -> ToolResult[CodeSnippet]:
        return self._container.read_range.execute(
            ReadRangeRequest(path, start_line, end_line, max_chars)
        )
