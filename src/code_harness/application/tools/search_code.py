from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from fnmatch import fnmatchcase
from time import perf_counter

from code_harness.application.dto.requests import (
    FindReferencesRequest,
    FindSymbolRequest,
    SearchCodeRequest,
    SearchFilesRequest,
    SearchTextRequest,
    SemanticSearchRequest,
)
from code_harness.application.ranking import HybridCandidate, HybridRanker, QueryClassifier
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.application.tools.search_files import SearchFilesTool
from code_harness.application.tools.search_text import SearchTextTool
from code_harness.application.tools.semantic_search import SemanticSearchTool
from code_harness.application.tools.structural import FindReferencesTool, FindSymbolTool
from code_harness.domain.enums import CapabilityState, MatchType, QueryKind
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.capability import StrategyOutcome, ToolWarning
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import HybridSearchHit, QueryClassification
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult, as_tool_warning, normalize_warnings
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore

_StrategyResult = tuple[list[HybridCandidate], list[ToolWarning], bool, StrategyOutcome]


def _matches_globs(path: str, includes: tuple[str, ...], excludes: tuple[str, ...]) -> bool:
    if includes and not any(fnmatchcase(path, pattern) for pattern in includes):
        return False
    return not any(fnmatchcase(path, pattern) for pattern in excludes)


class SearchCodeTool:
    def __init__(
        self,
        lexical: SearchTextTool,
        symbols: FindSymbolTool,
        references: FindReferencesTool,
        semantic: SemanticSearchTool,
        paths: SearchFilesTool,
        reader: IndexSourceReader,
        *,
        project: Project | None = None,
        store: RepositoryStore | None = None,
        classifier: QueryClassifier | None = None,
        ranker: HybridRanker | None = None,
    ) -> None:
        self._lexical = lexical
        self._symbols = symbols
        self._references = references
        self._semantic = semantic
        self._paths = paths
        self._reader = reader
        self._project = project
        self._store = store
        self._classifier = classifier or QueryClassifier()
        self._ranker = ranker or HybridRanker()

    def execute(self, request: SearchCodeRequest) -> ToolResult[tuple[HybridSearchHit, ...]]:
        classification = self._classifier.classify(request.query)
        index_state = resolve_index_state(self._store, self._project)

        def search() -> tuple[
            tuple[HybridSearchHit, ...],
            tuple[ToolWarning, ...],
            bool,
            tuple[StrategyOutcome, ...],
        ]:
            tasks: dict[str, Callable[[], _StrategyResult]] = {
                "fts": lambda: self._lexical_candidates(request, classification),
                "symbol": lambda: self._symbol_candidates(request, classification),
                "references": lambda: self._reference_candidates(request, classification),
                "paths": lambda: self._path_candidates(request, classification),
            }
            if classification.kind is not QueryKind.EXACT:
                tasks["semantic"] = lambda: self._semantic_candidates(request)

            candidates: list[HybridCandidate] = []
            warnings: list[ToolWarning] = []
            strategies: list[StrategyOutcome] = []
            truncated = False
            with ThreadPoolExecutor(
                max_workers=len(tasks),
                thread_name_prefix="hybrid-search",
            ) as pool:
                futures = {pool.submit(operation): name for name, operation in tasks.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        found, strategy_warnings, strategy_truncated, outcome = future.result()
                    except CodeHarnessError as error:
                        warning = ToolWarning(
                            code=error.code.value,
                            message=(
                                f"Hybrid {name} strategy is unavailable ({error.code.value}): "
                                f"{error.message}"
                            ),
                            recoverable=True,
                            capability=error.capability or name,
                            remediation=error.remediation,
                        )
                        warnings.append(warning)
                        strategies.append(
                            StrategyOutcome(
                                strategy=name if name != "fts" else "fts",
                                state=CapabilityState.UNAVAILABLE,
                                warning=warning,
                                error_code=error.code.value,
                            )
                        )
                        continue
                    candidates.extend(found)
                    warnings.extend(strategy_warnings)
                    strategies.append(outcome)
                    truncated = truncated or strategy_truncated

            # Prefer stable strategy order in responses.
            order = {"symbol": 0, "fts": 1, "ripgrep": 2, "references": 3, "paths": 4, "semantic": 5}
            strategies.sort(key=lambda item: order.get(item.strategy, 99))

            valid, validation_warnings = self._validate_candidates(request, candidates)
            warnings.extend(validation_warnings)
            ranked = self._ranker.rank(
                request.query,
                classification,
                valid,
                max_results=request.max_results,
            )
            materialized, materialization_warnings = self._materialize(ranked)
            warnings.extend(materialization_warnings)
            return (
                materialized,
                normalize_warnings(warnings),
                truncated or len(valid) > len(materialized),
                tuple(strategies),
            )

        result, elapsed_ms = timed(search)
        hits, warnings, truncated, strategies = result
        return ToolResult(
            hits,
            elapsed_ms,
            truncated=truncated,
            warnings=warnings,
            index_state=index_state,
            strategies=strategies,
        )

    def _lexical_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        index_states: list[str | None] = []
        per_term = max(request.max_results * 2, 20)
        for term in classification.lexical_terms[:5]:
            result = self._lexical.execute(
                SearchTextRequest(
                    term,
                    request.include_globs,
                    request.exclude_globs,
                    per_term,
                    request.context_lines,
                    False,
                    request.timeout_seconds,
                )
            )
            warnings.extend(normalize_warnings(result.warnings, capability="lexical"))
            truncated = truncated or result.truncated
            index_states.append(result.index_state)
            candidates.extend(
                HybridCandidate(
                    hit.snippet,
                    hit.match_type,
                    hit.score,
                    hit.matched_terms,
                    hit.reason or "Lexical match.",
                )
                for hit in result.data
            )
        state = CapabilityState.READY
        if not candidates and any(
            warning.code in {"ripgrep_unavailable", "embedding_unavailable"}
            for warning in warnings
        ):
            state = CapabilityState.UNAVAILABLE
        return (
            candidates,
            warnings,
            truncated,
            StrategyOutcome(
                strategy="fts",
                state=state,
                hit_count=len(candidates),
                elapsed_ms=int((perf_counter() - started) * 1000),
            ),
        )

    def _symbol_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        for term in classification.identifiers[:5]:
            result = self._symbols.execute(
                FindSymbolRequest(term, max(request.max_results * 2, 20), False)
            )
            warnings.extend(normalize_warnings(result.warnings, capability="structural"))
            truncated = truncated or result.truncated
            for item in result.data:
                symbol = item.symbol
                if symbol is None:
                    continue
                folded_term = term.casefold()
                names = {symbol.name.casefold(), (symbol.qualified_name or "").casefold()}
                if folded_term in names:
                    score = 1.0
                elif any(name.startswith(folded_term) for name in names):
                    score = 0.90
                else:
                    score = 0.75
                candidates.append(
                    HybridCandidate(
                        CodeSnippet(
                            symbol.location,
                            item.content,
                            None,
                            item.file_hash,
                        ),
                        MatchType.SYMBOL,
                        score,
                        (term,),
                        "Structural symbol match.",
                        symbol.symbol_id,
                        symbol.name,
                    )
                )
        return (
            candidates,
            warnings,
            truncated,
            StrategyOutcome(
                strategy="symbol",
                state=CapabilityState.READY,
                hit_count=len(candidates),
                elapsed_ms=int((perf_counter() - started) * 1000),
            ),
        )

    def _reference_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        for term in classification.identifiers[:5]:
            result = self._references.execute(
                FindReferencesRequest(
                    term,
                    max(request.max_results * 2, 20),
                    request.include_globs,
                    request.exclude_globs,
                    request.timeout_seconds,
                )
            )
            warnings.extend(normalize_warnings(result.warnings, capability="structural"))
            truncated = truncated or result.truncated
            for item in result.data:
                reference = item.reference
                if reference is None:
                    continue
                candidates.append(
                    HybridCandidate(
                        CodeSnippet(
                            reference.location,
                            item.content,
                            None,
                            item.file_hash,
                        ),
                        MatchType.REFERENCE,
                        0.85 if reference.kind not in {"textual", "unknown_textual"} else 0.70,
                        (term,),
                        "Reference to a requested identifier.",
                        reference.reference_id,
                        reference.target_name,
                    )
                )
        return (
            candidates,
            warnings,
            truncated,
            StrategyOutcome(
                strategy="references",
                state=CapabilityState.READY,
                hit_count=len(candidates),
                elapsed_ms=int((perf_counter() - started) * 1000),
            ),
        )

    def _semantic_candidates(self, request: SearchCodeRequest) -> _StrategyResult:
        started = perf_counter()
        try:
            result = self._semantic.execute(
                SemanticSearchRequest(
                    request.query,
                    request.include_globs,
                    request.exclude_globs,
                    request.languages,
                    max(request.max_results * 2, 20),
                )
            )
        except CodeHarnessError as error:
            warning = ToolWarning(
                code=error.code.value,
                message="Semantic strategy was skipped.",
                recoverable=True,
                capability="semantic",
                remediation=error.remediation
                or "Install compatible dependencies or disable semantic search.",
            )
            return (
                [],
                [warning],
                False,
                StrategyOutcome(
                    strategy="semantic",
                    state=CapabilityState.UNAVAILABLE,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                    warning=warning,
                    error_code=error.code.value,
                ),
            )
        warnings = list(normalize_warnings(result.warnings, capability="semantic"))
        state = CapabilityState.READY
        if not result.data and any(
            warning.code == "embedding_unavailable" for warning in warnings
        ):
            state = CapabilityState.UNAVAILABLE
        return (
            [
                HybridCandidate(
                    hit.snippet,
                    MatchType.SEMANTIC,
                    hit.score,
                    hit.matched_terms,
                    hit.reason or "Semantic similarity.",
                )
                for hit in result.data
            ],
            warnings,
            result.truncated,
            StrategyOutcome(
                strategy="semantic",
                state=state,
                hit_count=len(result.data),
                elapsed_ms=int((perf_counter() - started) * 1000),
            ),
        )

    def _path_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        started = perf_counter()
        terms = classification.path_terms or classification.identifiers[:2]
        candidates: list[HybridCandidate] = []
        truncated = False
        for term in terms:
            result = self._paths.execute(
                SearchFilesRequest(
                    term,
                    request.include_globs,
                    request.exclude_globs,
                    max(request.max_results, 20),
                    False,
                )
            )
            truncated = truncated or result.truncated
            for match in result.data:
                source = self._reader.load(match.source_file.path)
                lines = source.content.splitlines(keepends=True)
                end_line = min(80, max(1, len(lines)))
                candidates.append(
                    HybridCandidate(
                        CodeSnippet(
                            CodeLocation(source.path, 1, end_line),
                            "".join(lines[:end_line]),
                            source.language,
                            source.content_hash,
                        ),
                        MatchType.PATH,
                        match.score,
                        (term,),
                        match.reason,
                        source.path,
                        source.path,
                    )
                )
        return (
            candidates,
            [],
            truncated,
            StrategyOutcome(
                strategy="paths",
                state=CapabilityState.READY,
                hit_count=len(candidates),
                elapsed_ms=int((perf_counter() - started) * 1000),
            ),
        )

    def _validate_candidates(
        self,
        request: SearchCodeRequest,
        candidates: list[HybridCandidate],
    ) -> tuple[tuple[HybridCandidate, ...], tuple[ToolWarning, ...]]:
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        valid: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        languages = {language.casefold() for language in request.languages}
        for candidate in candidates:
            location = candidate.snippet.location
            if not _matches_globs(location.path, request.include_globs, request.exclude_globs):
                continue
            if location.path not in sources:
                try:
                    sources[location.path] = self._reader.load(location.path)
                except CodeHarnessError as error:
                    sources[location.path] = error
            source = sources[location.path]
            if isinstance(source, CodeHarnessError):
                warnings.append(
                    as_tool_warning(
                        f"Skipped {location.path}: current file is unavailable "
                        f"({source.code.value}).",
                        code=source.code.value,
                        capability="filesystem",
                    )
                )
                continue
            if languages and (source.language or "").casefold() not in languages:
                continue
            if source.content_hash != candidate.snippet.file_hash:
                warnings.append(
                    as_tool_warning(
                        f"Skipped stale hybrid result for {location.path}; reindex it.",
                        code="stale_structural_result",
                        capability="structural",
                    )
                )
                continue
            lines = source.content.splitlines(keepends=True)
            end_line = min(location.end_line, max(1, len(lines)))
            snippet = CodeSnippet(
                CodeLocation(location.path, location.start_line, end_line),
                "".join(lines[location.start_line - 1 : end_line]),
                source.language,
                source.content_hash,
            )
            valid.append(replace(candidate, snippet=snippet))
        return tuple(valid), normalize_warnings(warnings)

    def _materialize(
        self,
        hits: tuple[HybridSearchHit, ...],
    ) -> tuple[tuple[HybridSearchHit, ...], tuple[ToolWarning, ...]]:
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        materialized: list[HybridSearchHit] = []
        warnings: list[ToolWarning] = []
        for hit in hits:
            location = hit.snippet.location
            if location.path not in sources:
                try:
                    sources[location.path] = self._reader.load(location.path)
                except CodeHarnessError as error:
                    sources[location.path] = error
            source = sources[location.path]
            if isinstance(source, CodeHarnessError):
                warnings.append(
                    as_tool_warning(
                        f"Skipped {location.path}: current file is unavailable "
                        f"({source.code.value}).",
                        code=source.code.value,
                        capability="filesystem",
                    )
                )
                continue
            if source.content_hash != hit.snippet.file_hash:
                warnings.append(
                    as_tool_warning(
                        f"Skipped stale hybrid result for {location.path}; reindex it.",
                        code="stale_structural_result",
                        capability="structural",
                    )
                )
                continue
            lines = source.content.splitlines(keepends=True)
            end_line = min(location.end_line, max(1, len(lines)))
            snippet = CodeSnippet(
                CodeLocation(location.path, location.start_line, end_line),
                "".join(lines[location.start_line - 1 : end_line]),
                source.language,
                source.content_hash,
            )
            materialized.append(replace(hit, snippet=snippet))
        return tuple(materialized), normalize_warnings(warnings)
