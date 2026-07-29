"""Thin MCP handlers that translate protocol calls into application tools."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Annotated, Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from pydantic.json_schema import WithJsonSchema

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
from code_harness.domain.errors import CodeHarnessError, InternalToolError, InvalidQueryError
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.mcp.serializers import (
    ResponseDetail,
    resolve_response_detail,
    serialize_error,
    serialize_projected_result,
)

_RESPONSE_DETAIL_VALUES = [item.value for item in ResponseDetail]
_LOGGER = logging.getLogger(__name__)
ResponseDetailParameter = Annotated[
    str | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "enum": _RESPONSE_DETAIL_VALUES},
                {"type": "null"},
            ],
            "description": (
                "Response projection. Precedence: explicit argument, "
                "CODE_HARNESS_RESPONSE_DETAIL, compact."
            ),
        }
    ),
]


def _as_tuple(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(values or ())


def _operation_name(operation: Callable[[], ToolResult[Any]]) -> str:
    parts = [
        part
        for part in operation.__qualname__.split(".")
        if part not in {"<locals>", "operation", "execute"}
    ]
    if not parts:
        return "unknown"
    name = parts[-1]
    if name.endswith("Tool"):
        name = name[:-4]
    result: list[str] = []
    for index, character in enumerate(name):
        if index and character.isupper() and not name[index - 1].isupper():
            result.append("_")
        result.append(character.casefold())
    return "".join(result)


def _execute_operation(
    container: ApplicationContainer,
    operation: Callable[[], ToolResult[Any]],
    response_detail: ResponseDetail | str | None = None,
) -> dict[str, Any]:
    try:
        detail = resolve_response_detail(response_detail)
        return serialize_projected_result(
            container.with_index_state(operation()),
            detail,
        )
    except ValueError as error:
        return serialize_error(InvalidQueryError(str(error)))
    except CodeHarnessError as error:
        return serialize_error(error)
    except Exception:
        tool = _operation_name(operation)
        error_id = uuid4().hex
        _LOGGER.exception("Unexpected %s failure (error_id=%s).", tool, error_id)
        return serialize_error(InternalToolError(tool, error_id))


def register_handlers(
    server: FastMCP,
    container: ApplicationContainer,
    settings: Settings,
) -> None:
    def _execute(
        operation: Callable[[], ToolResult[Any]],
        response_detail: ResponseDetail | str | None = None,
    ) -> dict[str, Any]:
        return _execute_operation(container, operation, response_detail)

    @server.tool()
    def list_files(
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 10_000,
        cursor: str | None = None,
        sort: str = "path",
        sort_direction: str = "asc",
        include_total_count: bool = True,
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def search_files(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        case_sensitive: bool = False,
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def search_text(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def search_regex(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def read_file(
        path: str,
        max_chars: int = 200_000,
        max_lines: int = 5_000,
        include_line_numbers: bool = False,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Read a source file from the active project."""

        def operation() -> ToolResult[Any]:
            request = ReadFileRequest(path, max_chars, max_lines, include_line_numbers)
            return container.read_file.execute(request)

        return _execute(operation, response_detail)

    @server.tool()
    def read_range(
        path: str,
        start_line: int,
        end_line: int,
        max_chars: int = 200_000,
        include_line_numbers: bool = False,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Read an inclusive line range from a source file."""

        def operation() -> ToolResult[Any]:
            request = ReadRangeRequest(path, start_line, end_line, max_chars, include_line_numbers)
            return container.read_range.execute(request)

        return _execute(operation, response_detail)

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
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

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
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def find_references(
        query: str,
        max_results: int = 100,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        timeout_seconds: float = 10.0,
        include_comments: bool = True,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Find structural and textual references to a symbol."""

        def operation() -> ToolResult[Any]:
            request = FindReferencesRequest(
                query,
                max_results,
                _as_tuple(include_globs),
                _as_tuple(exclude_globs),
                timeout_seconds,
                include_comments,
            )
            return container.find_references.execute(request)

        return _execute(operation, response_detail)

    @server.tool()
    def semantic_search(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_results: int = 50,
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def search_code(
        query: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        languages: list[str] | None = None,
        max_results: int = 50,
        context_lines: int = 2,
        timeout_seconds: float = 10.0,
        snippet_mode: str = "match_window",
        max_snippet_lines: int = 40,
        max_snippet_chars: int = 6_000,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Hybrid search across lexical, structural, and optional semantic strategies."""

        def operation() -> ToolResult[Any]:
            request = SearchCodeRequest(
                query=query,
                include_globs=_as_tuple(include_globs),
                exclude_globs=_as_tuple(exclude_globs),
                languages=_as_tuple(languages),
                max_results=max_results,
                context_lines=context_lines,
                timeout_seconds=timeout_seconds,
                snippet_mode=snippet_mode,
                max_snippet_lines=max_snippet_lines,
                max_snippet_chars=max_snippet_chars,
            )
            return container.search_code.execute(request)

        return _execute(operation, response_detail)

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
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

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
        response_detail: ResponseDetailParameter = None,
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

        return _execute(operation, response_detail)

    @server.tool()
    def get_index_status(response_detail: ResponseDetailParameter = None) -> dict[str, Any]:
        """Return index state and statistics for the active project."""
        return _execute(container.get_index_status.execute, response_detail)

    if settings.mcp_expose_index_commands:

        @server.tool()
        def index_project(
            mode: str = IndexMode.INCREMENTAL.value,
            include_globs: list[str] | None = None,
            exclude_globs: list[str] | None = None,
            response_detail: ResponseDetailParameter = None,
        ) -> dict[str, Any]:
            """Create or update the local project index."""

            def operation() -> ToolResult[Any]:
                try:
                    index_mode = IndexMode(mode)
                except ValueError as error:
                    raise InvalidQueryError(
                        f"Unsupported index mode: {mode}",
                        mode=mode,
                    ) from error
                request = IndexProjectRequest(
                    index_mode,
                    include_globs=_as_tuple(include_globs),
                    exclude_globs=_as_tuple(exclude_globs),
                )
                return container.index_project.execute(request)

            return _execute(operation, response_detail)

    if settings.execution_enabled and settings.mcp_expose_execution:
        from code_harness.interfaces.mcp.execution_handlers import (
            register_execution_handlers,
        )

        register_execution_handlers(server, container, settings)
