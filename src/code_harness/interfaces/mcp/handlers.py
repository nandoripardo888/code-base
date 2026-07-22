"""Thin MCP handlers that translate protocol calls into application tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP

from code_harness.application.dto.requests import (
    BuildContextRequest,
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
from code_harness.bootstrap.container import ApplicationContainer
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode
from code_harness.domain.errors import CodeHarnessError, InvalidQueryError
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.mcp.serializers import serialize_error, serialize_tool_result


def _as_tuple(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(values or ())


def register_handlers(
    server: FastMCP,
    container: ApplicationContainer,
    settings: Settings,
) -> None:
    def _execute(operation: Callable[[], ToolResult[Any]]) -> dict[str, Any]:
        try:
            return serialize_tool_result(container.with_index_state(operation()))
        except ValueError as error:
            return serialize_error(InvalidQueryError(str(error)))
        except CodeHarnessError as error:
            return serialize_error(error)

    @server.tool()
    def list_files(
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 10_000,
        cursor: str | None = None,
        sort: str = "path",
        sort_direction: str = "asc",
        include_total_count: bool = True,
    ) -> dict[str, Any]:
        """List source files in the active project."""

        def operation() -> ToolResult[Any]:
            request = ListFilesRequest(
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                max_results,
                cursor,
                sort,
                sort_direction,
                include_total_count,
            )
            return container.list_files.execute(request)

        return _execute(operation)

    @server.tool()
    def search_files(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        """Search files by name or path fragment."""

        def operation() -> ToolResult[Any]:
            request = SearchFilesRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                max_results,
                case_sensitive,
            )
            return container.search_files.execute(request)

        return _execute(operation)

    @server.tool()
    def search_text(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Search source content for a literal string."""

        def operation() -> ToolResult[Any]:
            request = SearchTextRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
            return container.search_text.execute(request)

        return _execute(operation)

    @server.tool()
    def search_regex(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Search source content with a regular expression."""

        def operation() -> ToolResult[Any]:
            request = SearchRegexRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
            return container.search_regex.execute(request)

        return _execute(operation)

    @server.tool()
    def read_file(
        path: str,
        max_chars: int = 200_000,
        max_lines: int = 5_000,
        include_line_numbers: bool = False,
    ) -> dict[str, Any]:
        """Read a source file from the active project."""

        def operation() -> ToolResult[Any]:
            request = ReadFileRequest(path, max_chars, max_lines, include_line_numbers)
            return container.read_file.execute(request)

        return _execute(operation)

    @server.tool()
    def read_range(
        path: str,
        start_line: int,
        end_line: int,
        max_chars: int = 200_000,
        include_line_numbers: bool = False,
    ) -> dict[str, Any]:
        """Read an inclusive line range from a source file."""

        def operation() -> ToolResult[Any]:
            request = ReadRangeRequest(
                path, start_line, end_line, max_chars, include_line_numbers
            )
            return container.read_range.execute(request)

        return _execute(operation)

    @server.tool()
    def get_file_outline(
        path: str,
        include_content: bool | None = None,
        response_format: str = "compact",
        include_signatures: bool = True,
        max_symbols: int | None = None,
        max_depth: int | None = None,
        symbol_kinds: list[str] | None = None,
        max_content_chars_per_symbol: int | None = None,
    ) -> dict[str, Any]:
        """Return structural outline symbols for a file."""

        def operation() -> ToolResult[Any]:
            request = GetFileOutlineRequest(
                path,
                include_content=include_content,
                response_format=response_format,
                include_signatures=include_signatures,
                max_symbols=max_symbols,
                max_depth=max_depth,
                symbol_kinds=_as_tuple(symbol_kinds),
                max_content_chars_per_symbol=max_content_chars_per_symbol,
            )
            return container.get_file_outline.execute(request)

        return _execute(operation)

    @server.tool()
    def find_symbol(
        query: str,
        max_results: int = 50,
        exact: bool = False,
        include_content: bool | None = None,
        response_format: str = "compact",
        max_content_chars_per_symbol: int | None = None,
        kind: str | None = None,
        path: str | None = None,
        language: str | None = None,
        parameter_count: int | None = None,
    ) -> dict[str, Any]:
        """Find symbols by name or qualified name."""

        def operation() -> ToolResult[Any]:
            request = FindSymbolRequest(
                query,
                max_results,
                exact,
                include_content=include_content,
                response_format=response_format,
                max_content_chars_per_symbol=max_content_chars_per_symbol,
                kind=kind,
                path=path,
                language=language,
                parameter_count=parameter_count,
            )
            return container.find_symbol.execute(request)

        return _execute(operation)

    @server.tool()
    def find_references(
        query: str,
        max_results: int = 100,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Find structural and textual references to a symbol."""

        def operation() -> ToolResult[Any]:
            request = FindReferencesRequest(
                query,
                max_results,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                timeout_seconds,
            )
            return container.find_references.execute(request)

        return _execute(operation)

    @server.tool()
    def semantic_search(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search chunks by meaning when semantic indexing is available."""

        def operation() -> ToolResult[Any]:
            request = SemanticSearchRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                _as_tuple(languages),
                max_results,
            )
            return container.semantic_search.execute(request)

        return _execute(operation)

    @server.tool()
    def search_code(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 2,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Hybrid search across lexical, structural, and optional semantic strategies."""

        def operation() -> ToolResult[Any]:
            request = SearchCodeRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                _as_tuple(languages),
                max_results,
                context_lines,
                timeout_seconds,
            )
            return container.search_code.execute(request)

        return _execute(operation)

    @server.tool()
    def build_context(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_tokens: int = 12_000,
        reserved_tokens: int = 0,
        max_files: int = 12,
        max_snippets: int = 20,
        max_expansion_depth: int = 2,
    ) -> dict[str, Any]:
        """Build a budgeted context bundle for a query."""

        def operation() -> ToolResult[Any]:
            request = BuildContextRequest(
                query,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                _as_tuple(languages),
                max_tokens,
                reserved_tokens,
                max_files,
                max_snippets,
                max_expansion_depth,
            )
            return container.build_context.execute(request)

        return _execute(operation)

    @server.tool()
    def get_repository_map(
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_files: int = 200,
        max_symbols_per_file: int = 10,
        mode: str = "detailed",
        path: str | None = None,
        max_depth: int | None = None,
        cursor: str | None = None,
        include_files: bool = True,
        include_symbols: bool | None = None,
    ) -> dict[str, Any]:
        """Return a hierarchical repository map with validated symbols."""

        def operation() -> ToolResult[Any]:
            request = GetRepositoryMapRequest(
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                _as_tuple(languages),
                max_files,
                max_symbols_per_file,
                mode=mode,
                path=path,
                max_depth=max_depth,
                cursor=cursor,
                include_files=include_files,
                include_symbols=include_symbols,
            )
            return container.get_repository_map.execute(request)

        return _execute(operation)

    @server.tool()
    def get_index_status() -> dict[str, Any]:
        """Return index state and statistics for the active project."""
        return _execute(container.get_index_status.execute)

    if settings.mcp_expose_index_commands:

        @server.tool()
        def index_project(mode: str = IndexMode.INCREMENTAL.value) -> dict[str, Any]:
            """Create or update the local project index."""

            def operation() -> ToolResult[Any]:
                try:
                    index_mode = IndexMode(mode)
                except ValueError as error:
                    raise InvalidQueryError(
                        f"Unsupported index mode: {mode}",
                        mode=mode,
                    ) from error
                request = IndexProjectRequest(index_mode)
                return container.index_project.execute(request)

            return _execute(operation)
