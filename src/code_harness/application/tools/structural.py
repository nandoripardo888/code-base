from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256

from code_harness.application.dto.requests import (
    FindDefinitionRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetFileOutlineRequest,
)
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import IndexState
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import CodeReference, StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.text_searcher import TextSearcher


class _StructuralTool:
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        reader: IndexSourceReader,
    ) -> None:
        self._project = project
        self._store = store
        self._reader = reader

    def _execute(
        self,
        operation: Callable[[], tuple[StructuralSearchResult, ...]],
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        status = self._store.get_status(self._project)
        if (
            status.state not in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
            or not status.structural_schema_ready
        ):
            return ToolResult(
                (),
                0,
                warnings=("Structural index is not ready; run index first.",),
                index_state=status.state.value,
            )
        results, elapsed_ms = timed(operation)
        validated, warnings = self._validate(results)
        return ToolResult(
            validated,
            elapsed_ms,
            truncated=len(validated) < len(results),
            warnings=warnings,
            index_state=status.state.value,
        )

    def _validate(
        self, results: tuple[StructuralSearchResult, ...]
    ) -> tuple[tuple[StructuralSearchResult, ...], tuple[str, ...]]:
        sources: dict[str, IndexedSource | None] = {}
        valid: list[StructuralSearchResult] = []
        warnings: list[str] = []
        for result in results:
            item = result.symbol or result.reference
            if item is None:
                continue
            location = item.location
            if location.path not in sources:
                try:
                    sources[location.path] = self._reader.load(location.path)
                except CodeHarnessError as error:
                    sources[location.path] = None
                    warnings.append(
                        f"Skipped {location.path}: current file is unavailable "
                        f"({error.code.value})."
                    )
            source = sources[location.path]
            if source is None:
                continue
            if source.content_hash != result.file_hash:
                warnings.append(f"Skipped stale structural result for {location.path}; reindex it.")
                continue
            lines = source.content.splitlines(keepends=True)
            content = "".join(lines[location.start_line - 1 : location.end_line])
            valid.append(replace(result, content=content))
        return tuple(valid), tuple(dict.fromkeys(warnings))


class GetFileOutlineTool(_StructuralTool):
    def execute(
        self, request: GetFileOutlineRequest
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        self._reader.load(request.path)
        return self._execute(
            lambda: self._store.get_outline(self._project.project_id, request.path)
        )


class FindSymbolTool(_StructuralTool):
    def execute(self, request: FindSymbolRequest) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._execute(
            lambda: self._store.find_symbols(
                self._project.project_id,
                request.query,
                exact=request.exact,
                limit=request.max_results,
            )
        )


class FindDefinitionTool(_StructuralTool):
    def execute(
        self, request: FindDefinitionRequest
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._execute(
            lambda: self._store.find_symbols(
                self._project.project_id,
                request.query,
                exact=True,
                limit=request.max_results,
            )
        )


class FindReferencesTool(_StructuralTool):
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        reader: IndexSourceReader,
        lexical_searcher: TextSearcher,
    ) -> None:
        super().__init__(project, store, reader)
        self._lexical_searcher = lexical_searcher

    def execute(
        self, request: FindReferencesRequest
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        status = self._store.get_status(self._project)
        collected_warnings: list[str] = []

        def search() -> tuple[StructuralSearchResult, ...]:
            structural = (
                self._store.find_references(
                    self._project.project_id,
                    request.query,
                    limit=request.max_results,
                )
                if status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
                and status.structural_schema_ready
                else ()
            )
            validated, validation_warnings = self._validate(structural)
            collected_warnings.extend(validation_warnings)
            lexical = self._lexical_searcher.search(
                query=request.query,
                regex=False,
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
                case_sensitive=False,
                max_results=request.max_results,
                context_lines=0,
                timeout_seconds=request.timeout_seconds,
            )
            collected_warnings.extend(lexical.warnings)
            combined: list[StructuralSearchResult] = list(validated)
            seen = {
                (
                    item.reference.location.path,
                    item.reference.location.start_line,
                )
                for item in validated
                if item.reference is not None
            }
            for hit in lexical.hits:
                location = hit.snippet.location
                key = (location.path, location.start_line)
                if key in seen:
                    continue
                seen.add(key)
                reference_id = sha256(
                    f"{location.path}\x1f{location.start_line}\x1f{request.query}".encode()
                ).hexdigest()[:32]
                combined.append(
                    StructuralSearchResult(
                        None,
                        CodeReference(reference_id, request.query, "textual", location),
                        hit.snippet.content,
                        hit.snippet.file_hash,
                    )
                )
                if len(combined) >= request.max_results:
                    break
            return tuple(combined)

        results, elapsed_ms = timed(search)
        if (
            status.state not in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
            or not status.structural_schema_ready
        ):
            collected_warnings.append(
                "Structural index is not ready; returned lexical references only."
            )
        return ToolResult(
            results,
            elapsed_ms,
            truncated=len(results) >= request.max_results,
            warnings=tuple(dict.fromkeys(collected_warnings)),
            index_state=status.state.value,
        )
