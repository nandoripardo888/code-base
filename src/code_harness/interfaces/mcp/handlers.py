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
from code_harness.application.dto.review_requests import (
    ApplyReviewFixRequest,
    BuildReviewContextRequest,
    CreateReviewCommitRequest,
    FindChangeImpactsRequest,
    GetChangedSymbolsRequest,
    GetChangeSetRequest,
    ListChangedFilesRequest,
    PublishReviewRequest,
    ReadDiffRequest,
    SuggestValidationPlanRequest,
    ValidateChangeSetRequest,
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

    @server.tool()
    def get_change_set(
        source: str = "working_tree",
        base: str | None = "HEAD",
        include_untracked: bool = True,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Capture a read-only snapshot of repository changes."""

        def operation() -> ToolResult[Any]:
            return container.get_change_set.execute(
                GetChangeSetRequest(
                    source=source,
                    base=base,
                    include_untracked=include_untracked,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def list_changed_files(
        change_set_id: str,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """List files contained in a previously captured change set."""

        def operation() -> ToolResult[Any]:
            return container.list_changed_files.execute(
                ListChangedFilesRequest(change_set_id=change_set_id)
            )

        return _execute(operation, response_detail)

    @server.tool()
    def read_diff(
        change_set_id: str,
        path: str | None = None,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Return structured hunks for a change set."""

        def operation() -> ToolResult[Any]:
            return container.read_diff.execute(
                ReadDiffRequest(change_set_id=change_set_id, path=path)
            )

        return _execute(operation, response_detail)

    @server.tool()
    def get_changed_symbols(
        change_set_id: str,
        path: str | None = None,
        max_symbols: int = 200,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Map change-set hunks to indexed symbols when available."""

        def operation() -> ToolResult[Any]:
            return container.get_changed_symbols.execute(
                GetChangedSymbolsRequest(
                    change_set_id=change_set_id,
                    path=path,
                    max_symbols=max_symbols,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def find_change_impacts(
        change_set_id: str,
        path: str | None = None,
        max_symbols: int = 50,
        max_references_per_symbol: int = 30,
        max_tests_per_symbol: int = 10,
        include_config: bool = True,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Find callers, references, related tests and config for changed symbols."""

        def operation() -> ToolResult[Any]:
            return container.find_change_impacts.execute(
                FindChangeImpactsRequest(
                    change_set_id=change_set_id,
                    path=path,
                    max_symbols=max_symbols,
                    max_references_per_symbol=max_references_per_symbol,
                    max_tests_per_symbol=max_tests_per_symbol,
                    include_config=include_config,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def build_review_context(
        change_set_id: str,
        path: str | None = None,
        max_tokens: int = 12_000,
        reserved_tokens: int = 2_000,
        max_files: int = 15,
        max_snippets: int = 25,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Build a diff-first review context bundle under a token budget."""

        def operation() -> ToolResult[Any]:
            return container.build_review_context.execute(
                BuildReviewContextRequest(
                    change_set_id=change_set_id,
                    path=path,
                    max_tokens=max_tokens,
                    reserved_tokens=reserved_tokens,
                    max_files=max_files,
                    max_snippets=max_snippets,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def suggest_validation_plan(
        change_set_id: str,
        path: str | None = None,
        max_test_files: int = 20,
        response_detail: ResponseDetailParameter = None,
    ) -> dict[str, Any]:
        """Suggest a deterministic validation plan for a change set."""

        def operation() -> ToolResult[Any]:
            return container.suggest_validation_plan.execute(
                SuggestValidationPlanRequest(
                    change_set_id=change_set_id,
                    path=path,
                    max_test_files=max_test_files,
                )
            )

        return _execute(operation, response_detail)

    if settings.mcp_expose_review_actions:

        @server.tool()
        def validate_change_set(
            change_set_id: str,
            executable: str,
            args: list[str] | None = None,
            cwd: str = ".",
            timeout_seconds: float | None = None,
            max_output_bytes: int | None = None,
            requested_capabilities: list[str] | None = None,
            reason: str | None = None,
            approval_id: str | None = None,
            wait: bool = True,
            use_worktree: bool | None = None,
            response_detail: ResponseDetailParameter = None,
        ) -> dict[str, Any]:
            """Run an approved validation command bound to a change-set snapshot."""

            def operation() -> ToolResult[Any]:
                assert container.validate_change_set is not None
                return container.validate_change_set.execute(
                    ValidateChangeSetRequest(
                        change_set_id=change_set_id,
                        executable=executable,
                        args=tuple(args or ()),
                        cwd=cwd,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        requested_capabilities=tuple(requested_capabilities or ()),
                        reason=reason,
                        approval_id=approval_id,
                        wait=wait,
                        use_worktree=use_worktree,
                    )
                )

            return _execute(operation, response_detail)

        @server.tool()
        def apply_review_fix(
            change_set_id: str,
            patch_text: str,
            expected_file_hashes: list[list[str]],
            approval_id: str | None = None,
            reason: str | None = None,
            response_detail: ResponseDetailParameter = None,
        ) -> dict[str, Any]:
            """Apply an exact unified patch after snapshot hash checks."""

            def operation() -> ToolResult[Any]:
                assert container.apply_review_fix is not None
                hashes = tuple((item[0], item[1]) for item in expected_file_hashes)
                return container.apply_review_fix.execute(
                    ApplyReviewFixRequest(
                        change_set_id=change_set_id,
                        patch_text=patch_text,
                        expected_file_hashes=hashes,
                        approval_id=approval_id,
                        reason=reason,
                    )
                )

            return _execute(operation, response_detail)

        @server.tool()
        def publish_review(
            change_set_id: str,
            title: str,
            body: str = "",
            comments: list[dict[str, Any]] | None = None,
            approval_id: str | None = None,
            response_detail: ResponseDetailParameter = None,
        ) -> dict[str, Any]:
            """Publish a local review artifact (opt-in file publisher)."""

            def operation() -> ToolResult[Any]:
                assert container.publish_review is not None
                normalized = tuple(
                    (
                        str(item.get("path", "")),
                        str(item.get("body", "")),
                        item.get("start_line"),
                        item.get("end_line"),
                        str(item.get("side", "RIGHT")),
                    )
                    for item in (comments or [])
                )
                return container.publish_review.execute(
                    PublishReviewRequest(
                        change_set_id=change_set_id,
                        title=title,
                        body=body,
                        comments=normalized,
                        approval_id=approval_id,
                    )
                )

            return _execute(operation, response_detail)

        @server.tool()
        def create_review_commit(
            change_set_id: str,
            message: str,
            paths: list[str] | None = None,
            approval_id: str | None = None,
            response_detail: ResponseDetailParameter = None,
        ) -> dict[str, Any]:
            """Create a Git commit for reviewed changes (opt-in)."""

            def operation() -> ToolResult[Any]:
                assert container.create_review_commit is not None
                return container.create_review_commit.execute(
                    CreateReviewCommitRequest(
                        change_set_id=change_set_id,
                        message=message,
                        paths=tuple(paths) if paths is not None else None,
                        approval_id=approval_id,
                    )
                )

            return _execute(operation, response_detail)

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

    if settings.mcp_expose_change_sessions:
        from code_harness.interfaces.mcp.change_handlers import register_change_handlers

        register_change_handlers(server, container, settings)
