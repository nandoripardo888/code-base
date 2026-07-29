from __future__ import annotations

from pathlib import PurePosixPath

from code_harness.application.dto.requests import FindReferencesRequest, SearchFilesRequest
from code_harness.application.dto.review_requests import (
    FindChangeImpactsRequest,
    GetChangedSymbolsRequest,
)
from code_harness.application.review.get_changed_symbols import GetChangedSymbolsTool
from code_harness.application.tools._timing import timed
from code_harness.application.tools.search_files import SearchFilesTool
from code_harness.application.tools.structural import FindReferencesTool
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.change_set import ChangedSymbol
from code_harness.domain.models.review import (
    ChangeImpact,
    ImpactReference,
    ImpactTest,
)
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings

_CONFIGURATION_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
_CALLER_KINDS = {"call", "instantiation", "type_use"}
_TEST_GLOBS = ("tests/**", "**/test_*.py", "**/*_test.py", "**/test_*.sql")


class FindChangeImpactsTool:
    def __init__(
        self,
        *,
        changed_symbols: GetChangedSymbolsTool,
        find_references: FindReferencesTool,
        search_files: SearchFilesTool,
    ) -> None:
        self._changed_symbols = changed_symbols
        self._find_references = find_references
        self._search_files = search_files

    def execute(self, request: FindChangeImpactsRequest) -> ToolResult[tuple[ChangeImpact, ...]]:
        warnings: list[str | ToolWarning] = []

        def resolve() -> tuple[ChangeImpact, ...]:
            symbols_result = self._changed_symbols.execute(
                GetChangedSymbolsRequest(
                    change_set_id=request.change_set_id,
                    path=request.path,
                    max_symbols=request.max_symbols,
                )
            )
            warnings.extend(symbols_result.warnings)
            impacts: list[ChangeImpact] = []
            for symbol in symbols_result.data:
                impacts.append(self._impact_for_symbol(symbol, request, warnings))
            return tuple(impacts)

        impacts, elapsed_ms = timed(resolve)
        return ToolResult(
            impacts,
            elapsed_ms,
            warnings=normalize_warnings(warnings, capability="review"),
        )

    def _impact_for_symbol(
        self,
        symbol: ChangedSymbol,
        request: FindChangeImpactsRequest,
        warnings: list[str | ToolWarning],
    ) -> ChangeImpact:
        query = symbol.qualified_name or symbol.name
        refs_result = self._find_references.execute(
            FindReferencesRequest(
                query=query,
                max_results=request.max_references_per_symbol,
                include_comments=False,
            )
        )
        warnings.extend(refs_result.warnings)
        callers: list[ImpactReference] = []
        references: list[ImpactReference] = []
        configs: list[ImpactReference] = []
        implementations: list[ImpactReference] = []
        seen: set[tuple[str, int, str]] = set()
        for item in refs_result.data:
            mapped = _map_reference(item)
            if mapped is None:
                continue
            key = (mapped.path, mapped.start_line, mapped.kind)
            if key in seen:
                continue
            seen.add(key)
            if mapped.path == symbol.path and mapped.kind == "definition":
                continue
            references.append(mapped)
            suffix = PurePosixPath(mapped.path).suffix.casefold()
            if request.include_config and (
                mapped.kind == "configuration_textual" or suffix in _CONFIGURATION_SUFFIXES
            ):
                configs.append(mapped)
            elif mapped.kind in _CALLER_KINDS and mapped.path != symbol.path:
                callers.append(mapped)
            elif mapped.kind == "definition" and mapped.path != symbol.path:
                implementations.append(mapped)
        tests = self._related_tests(symbol, request.max_tests_per_symbol, warnings)
        return ChangeImpact(
            symbol_id=symbol.symbol_id,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            path=symbol.path,
            kind=symbol.kind,
            callers=tuple(callers),
            references=tuple(references),
            related_tests=tests,
            config_files=tuple(configs),
            implementations=tuple(implementations),
        )

    def _related_tests(
        self,
        symbol: ChangedSymbol,
        limit: int,
        warnings: list[str | ToolWarning],
    ) -> tuple[ImpactTest, ...]:
        candidates: dict[str, ImpactTest] = {}
        stem = PurePosixPath(symbol.path).stem
        queries = (
            f"test_{symbol.name}",
            f"test_{stem}",
            stem,
        )
        for query in queries:
            try:
                matches = self._search_files.execute(
                    SearchFilesRequest(
                        query=query,
                        include_globs=_TEST_GLOBS,
                        max_results=limit,
                    )
                )
            except Exception as error:  # pragma: no cover - defensive
                warnings.append(
                    ToolWarning(
                        code="test_discovery_failed",
                        message=str(error),
                        capability="review",
                        recoverable=True,
                    )
                )
                continue
            warnings.extend(matches.warnings)
            for match in matches.data:
                path = match.source_file.path
                if path in candidates:
                    continue
                reason = "name_match" if "test_" in PurePosixPath(path).name else "path_match"
                score = 1.0 if reason == "name_match" else 0.6
                if stem and stem in path.replace("\\", "/"):
                    reason = "same_module_mirror"
                    score = 0.85
                candidates[path] = ImpactTest(path=path, reason=reason, score=score)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break
        ordered = sorted(candidates.values(), key=lambda item: (-item.score, item.path))
        return tuple(ordered[:limit])


def _map_reference(item: StructuralSearchResult) -> ImpactReference | None:
    if item.reference is None:
        return None
    ref = item.reference
    return ImpactReference(
        path=ref.location.path,
        start_line=ref.location.start_line,
        end_line=ref.location.end_line,
        kind=ref.kind,
        confidence=ref.confidence,
        symbol_id=ref.source_symbol_id,
    )
