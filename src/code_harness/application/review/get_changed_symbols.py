from __future__ import annotations

from code_harness.application.dto.review_requests import GetChangedSymbolsRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.enums import IndexState
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.change_set import ChangedHunk, ChangedSymbol
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import CodeSymbol
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider
from code_harness.domain.protocols.repository_store import RepositoryStore


class GetChangedSymbolsTool:
    def __init__(
        self,
        *,
        project: Project,
        store: RepositoryStore,
        provider: ChangeProvider,
    ) -> None:
        self._project = project
        self._store = store
        self._provider = provider

    def execute(
        self,
        request: GetChangedSymbolsRequest,
    ) -> ToolResult[tuple[ChangedSymbol, ...]]:
        warnings: list[ToolWarning] = []

        def resolve() -> tuple[ChangedSymbol, ...]:
            change_set = self._provider.get_change_set(request.change_set_id)
            diff = self._provider.read_diff(change_set)
            files = diff.files
            if request.path is not None:
                files = tuple(
                    item
                    for item in files
                    if item.path == request.path or item.old_path == request.path
                )
            index_state = resolve_index_state(self._store, self._project)
            if index_state not in {
                IndexState.READY.value,
                IndexState.READY_WITH_WARNINGS.value,
            }:
                warnings.append(
                    ToolWarning(
                        code="structural_index_not_ready",
                        message="Structural index is not ready; changed symbols are unavailable.",
                        capability="structural",
                        recoverable=True,
                        remediation="Run index_project before get_changed_symbols.",
                    )
                )
                return ()
            found: list[ChangedSymbol] = []
            seen: set[tuple[str, str]] = set()
            for changed_file in files:
                if changed_file.binary:
                    continue
                outline = self._store.get_outline(self._project.project_id, changed_file.path)
                symbols = tuple(item.symbol for item in outline if item.symbol is not None)
                for hunk in changed_file.hunks:
                    for symbol in _symbols_for_hunk(symbols, hunk):
                        key = (symbol.symbol_id, hunk.hunk_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        found.append(
                            ChangedSymbol(
                                symbol_id=symbol.symbol_id,
                                path=symbol.location.path,
                                kind=symbol.kind,
                                name=symbol.name,
                                qualified_name=symbol.qualified_name or symbol.name,
                                start_line=symbol.location.start_line,
                                end_line=symbol.location.end_line,
                                hunk_ids=(hunk.hunk_id,),
                                side="new",
                            )
                        )
                        if len(found) >= request.max_symbols:
                            return tuple(found)
            return tuple(found)

        symbols, elapsed_ms = timed(resolve)
        return ToolResult(
            symbols,
            elapsed_ms,
            warnings=tuple(warnings),
            index_state=resolve_index_state(self._store, self._project),
        )


def _symbols_for_hunk(
    symbols: tuple[CodeSymbol, ...],
    hunk: ChangedHunk,
) -> tuple[CodeSymbol, ...]:
    if hunk.new_count <= 0:
        return ()
    lines = range(hunk.new_start, hunk.new_start + hunk.new_count)
    matched: dict[str, CodeSymbol] = {}
    for line in lines:
        owner = _innermost_symbol(symbols, line)
        if owner is not None:
            matched[owner.symbol_id] = owner
    return tuple(matched.values())


def _innermost_symbol(symbols: tuple[CodeSymbol, ...], line: int) -> CodeSymbol | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.location.start_line <= line <= symbol.location.end_line
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda symbol: (
            -symbol.location.start_line,
            symbol.location.end_line - symbol.location.start_line,
        )
    )
    return candidates[0]
