from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
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
from code_harness.application.tools.structural import (
    FindReferencesTool,
    FindSymbolTool,
    _classify_lexical_reference,
)
from code_harness.domain.enums import CapabilityState, MatchType, QueryKind
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.capability import StrategyOutcome, ToolWarning
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import HybridSearchHit, QueryClassification, QueryPlan
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.result_truncation import (
    TruncationReason,
    merge_truncations,
    truncation,
)
from code_harness.domain.models.tool_result import ToolResult, as_tool_warning, normalize_warnings
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore

_StrategyResult = tuple[list[HybridCandidate], list[ToolWarning], bool, StrategyOutcome]
_CONTAINER_KINDS = frozenset({"class", "interface", "enum", "record", "module", "package"})
_ENUMERATION_TERMS = frozenset(
    {
        "campos",
        "fields",
        "listar",
        "liste",
        "list",
        "members",
        "membros",
        "methods",
        "métodos",
        "quais",
        "which",
    }
)


def _matches_globs(path: str, includes: tuple[str, ...], excludes: tuple[str, ...]) -> bool:
    if includes and not any(fnmatchcase(path, pattern) for pattern in includes):
        return False
    return not any(fnmatchcase(path, pattern) for pattern in excludes)


def _search_terms(
    request: SearchCodeRequest,
    classification: QueryClassification,
    plan: QueryPlan | None = None,
) -> tuple[str, ...]:
    values = (
        *(plan.query_terms if plan is not None else ()),
        *classification.lexical_terms,
        *classification.identifiers,
        *request.query.split(),
    )
    return tuple(dict.fromkeys(value.strip().casefold() for value in values if value.strip()))


def _text_score(text: str, terms: tuple[str, ...]) -> tuple[int, int]:
    folded = text.casefold()
    counts = tuple(folded.count(term) for term in terms)
    return sum(count > 0 for count in counts), sum(counts)


def _best_line_window(
    lines: list[str],
    start_line: int,
    end_line: int,
    *,
    terms: tuple[str, ...],
    max_lines: int,
) -> tuple[int, int]:
    if end_line - start_line + 1 <= max_lines:
        return start_line, end_line
    latest_start = end_line - max_lines + 1
    candidate_starts = {start_line}
    radius = max_lines // 2
    for line_number in range(start_line, end_line + 1):
        if _text_score(lines[line_number - 1], terms) == (0, 0):
            continue
        candidate_starts.add(min(latest_start, max(start_line, line_number - radius)))
    best_start = max(
        (
            (
                *_text_score("".join(lines[value - 1 : value - 1 + max_lines]), terms),
                -value,
                value,
            )
            for value in candidate_starts
        ),
    )[-1]
    return best_start, best_start + max_lines - 1


def _long_line_slice(line: str, terms: tuple[str, ...], max_chars: int) -> str:
    folded = line.casefold()
    positions = [folded.find(term) for term in terms if term and folded.find(term) >= 0]
    if not positions:
        return line[:max_chars]
    offset = max(0, min(positions) - max_chars // 2)
    return line[offset : offset + max_chars]


def _best_char_window(
    lines: list[str],
    start_line: int,
    end_line: int,
    *,
    terms: tuple[str, ...],
    max_chars: int,
) -> tuple[str, int, int, bool]:
    selected = "".join(lines[start_line - 1 : end_line])
    if len(selected) <= max_chars:
        return selected, start_line, end_line, False

    candidates: list[tuple[int, int, int, int, str, int, int]] = []
    for candidate_start in range(start_line, end_line + 1):
        parts: list[str] = []
        size = 0
        candidate_end = candidate_start
        for line_number in range(candidate_start, end_line + 1):
            line = lines[line_number - 1]
            if len(line) > max_chars and not parts:
                part = _long_line_slice(line, terms, max_chars)
                distinct, occurrences = _text_score(part, terms)
                candidates.append(
                    (
                        distinct,
                        occurrences,
                        -candidate_start,
                        len(part),
                        part,
                        candidate_start,
                        candidate_start,
                    )
                )
                break
            if size + len(line) > max_chars:
                break
            parts.append(line)
            size += len(line)
            candidate_end = line_number
        if parts:
            content = "".join(parts)
            distinct, occurrences = _text_score(content, terms)
            candidates.append(
                (
                    distinct,
                    occurrences,
                    -candidate_start,
                    len(content),
                    content,
                    candidate_start,
                    candidate_end,
                )
            )
    if not candidates:
        return "", start_line, start_line, True
    best = max(candidates, key=lambda item: item[:4])
    return best[4], best[5], best[6], True


def _bounded_snippet(
    source: IndexedSource,
    location: CodeLocation,
    request: SearchCodeRequest,
    classification: QueryClassification,
    plan: QueryPlan | None = None,
) -> tuple[CodeSnippet, bool, CodeLocation | None]:
    lines = source.content.splitlines(keepends=True)
    if not lines:
        location = CodeLocation(location.path, 1, 1)
        return (
            CodeSnippet(location, "", source.language, source.content_hash),
            False,
            None,
        )
    total_lines = max(1, len(lines))
    start_line = min(location.start_line, total_lines)
    end_line = min(location.end_line, total_lines)
    original = CodeLocation(location.path, start_line, end_line)
    if request.snippet_mode == "none":
        return (
            CodeSnippet(original, "", source.language, source.content_hash),
            False,
            None,
        )

    terms = _search_terms(request, classification, plan)
    if request.snippet_mode == "symbol":
        window_start = start_line
        window_end = min(end_line, start_line + request.max_snippet_lines - 1)
    else:
        window_start, window_end = _best_line_window(
            lines,
            start_line,
            end_line,
            terms=terms,
            max_lines=request.max_snippet_lines,
        )
    content, actual_start, actual_end, char_truncated = _best_char_window(
        lines,
        window_start,
        window_end,
        terms=terms,
        max_chars=request.max_snippet_chars,
    )
    truncated = window_start != start_line or window_end != end_line or char_truncated
    return (
        CodeSnippet(
            CodeLocation(location.path, actual_start, actual_end),
            content,
            source.language,
            source.content_hash,
        ),
        truncated,
        original if truncated else None,
    )


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
        plan = self._build_query_plan(request, classification)
        index_state = resolve_index_state(self._store, self._project)

        def search() -> tuple[
            tuple[HybridSearchHit, ...],
            tuple[ToolWarning, ...],
            bool,
            bool,
            tuple[StrategyOutcome, ...],
        ]:
            tasks: dict[str, Callable[[], _StrategyResult]] = {
                "fts": lambda: self._lexical_candidates(request, classification, plan),
                "symbol": lambda: self._symbol_candidates(request, classification, plan),
                "references": lambda: self._reference_candidates(
                    request, classification, plan
                ),
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
            order = {
                "symbol": 0,
                "fts": 1,
                "ripgrep": 2,
                "references": 3,
                "paths": 4,
                "semantic": 5,
            }
            strategies.sort(key=lambda item: order.get(item.strategy, 99))

            valid, validation_warnings = self._validate_candidates(
                request,
                classification,
                candidates,
                plan,
            )
            warnings.extend(validation_warnings)
            valid = self._attach_path_evidence(valid)
            ranked = self._ranker.rank(
                request.query,
                classification,
                valid,
                max_results=request.max_results + 1,
                plan=plan,
            )
            materialized, materialization_warnings = self._materialize(
                ranked,
                request,
                classification,
                plan,
            )
            warnings.extend(materialization_warnings)
            has_extra = len(materialized) > request.max_results
            return (
                materialized[: request.max_results],
                normalize_warnings(warnings),
                truncated,
                has_extra,
                tuple(strategies),
            )

        result, elapsed_ms = timed(search)
        hits, warnings, candidates_truncated, results_truncated, strategies = result
        snippet_reasons: list[TruncationReason] = []
        if any(hit.snippet_truncated for hit in hits):
            if any(
                hit.source_location is not None
                and hit.source_location.end_line - hit.source_location.start_line + 1
                > request.max_snippet_lines
                for hit in hits
            ):
                snippet_reasons.append(TruncationReason.SNIPPET_LINE_LIMIT)
            if any(len(hit.snippet.content) >= request.max_snippet_chars for hit in hits):
                snippet_reasons.append(TruncationReason.SNIPPET_CHAR_LIMIT)
            if not snippet_reasons:
                snippet_reasons.append(TruncationReason.SNIPPET_CHAR_LIMIT)
        result_truncation = merge_truncations(
            (
                truncation(TruncationReason.CANDIDATE_LIMIT, candidates=True)
                if candidates_truncated
                else None
            ),
            (
                truncation(TruncationReason.RESULT_LIMIT, results=True)
                if results_truncated
                else None
            ),
            (truncation(*snippet_reasons, snippets=True) if snippet_reasons else None),
        )
        return ToolResult(
            hits,
            elapsed_ms,
            truncated=result_truncation is not None,
            truncation=result_truncation,
            warnings=warnings,
            index_state=index_state,
            strategies=strategies,
        )

    def _build_query_plan(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> QueryPlan:
        original = classification.original_identifiers or tuple(
            term
            for term in classification.identifiers
            if term.casefold() in request.query.casefold()
        )
        query_terms = tuple(dict.fromkeys(original or classification.lexical_terms))
        query_words = {
            word.casefold().strip("?!.,:;()[]{}")
            for word in request.query.split()
            if word.strip("?!.,:;()[]{}")
        }
        enumeration = bool(query_words & _ENUMERATION_TERMS)
        anchor_symbol = None
        if self._store is not None and self._project is not None:
            for term in original:
                try:
                    matches = self._store.find_symbols(
                        self._project.project_id,
                        term,
                        exact=True,
                        limit=50,
                    )
                except CodeHarnessError:
                    matches = ()
                containers = [
                    item.symbol
                    for item in matches
                    if item.symbol is not None
                    and item.symbol.kind.casefold() in _CONTAINER_KINDS
                    and term.casefold()
                    in {
                        item.symbol.name.casefold(),
                        (item.symbol.qualified_name or "").casefold(),
                    }
                ]
                if containers:
                    anchor_symbol = min(
                        containers,
                        key=lambda symbol: (
                            len(symbol.qualified_name or symbol.name),
                            symbol.location.path,
                            symbol.location.start_line,
                        ),
                    )
                    break

        anchor_name: str | None = None
        anchor_path: str | None = None
        anchor_symbol_id: str | None = None
        anchor_location: CodeLocation | None = None
        anchor_kind: str | None = None
        if anchor_symbol is not None:
            anchor_name = anchor_symbol.name
            anchor_path = anchor_symbol.location.path
            anchor_symbol_id = anchor_symbol.symbol_id
            anchor_location = anchor_symbol.location
            anchor_kind = anchor_symbol.kind
        else:
            for term in original:
                try:
                    path_matches = self._paths.execute(
                        SearchFilesRequest(
                            term,
                            request.include_globs,
                            request.exclude_globs,
                            50,
                            False,
                        )
                    )
                except CodeHarnessError:
                    continue
                exact = [
                    item
                    for item in path_matches.data
                    if PurePosixPath(item.source_file.path).stem.casefold() == term.casefold()
                ]
                if not exact:
                    continue
                selected = min(exact, key=lambda item: item.source_file.path)
                anchor_name = term
                anchor_path = selected.source_file.path
                try:
                    source = self._reader.load(anchor_path)
                except CodeHarnessError:
                    break
                anchor_location = CodeLocation(
                    anchor_path,
                    1,
                    max(1, len(source.content.splitlines())),
                )
                anchor_kind = "file"
                break

        target_terms = tuple(
            term
            for term in original
            if anchor_name is None or term.casefold() != anchor_name.casefold()
        )
        return QueryPlan(
            query_terms=query_terms,
            target_terms=target_terms,
            anchor_name=anchor_name,
            anchor_path=anchor_path,
            anchor_symbol_id=anchor_symbol_id,
            anchor_location=anchor_location,
            anchor_kind=anchor_kind,
            enumeration=enumeration,
        )

    @staticmethod
    def _scopes(
        request: SearchCodeRequest,
        plan: QueryPlan,
    ) -> tuple[tuple[str, ...], ...]:
        scopes: list[tuple[str, ...]] = []
        if (
            plan.anchor_path is not None
            and _matches_globs(
                plan.anchor_path,
                request.include_globs,
                request.exclude_globs,
            )
        ):
            scopes.append((plan.anchor_path,))
        scopes.append(request.include_globs)
        return tuple(dict.fromkeys(scopes))

    def _lexical_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
        plan: QueryPlan,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        index_states: list[str | None] = []
        per_term = max(request.max_results * 2, 20)
        terms = tuple(
            dict.fromkeys(
                (
                    *plan.target_terms,
                    *((plan.anchor_name,) if plan.anchor_name else ()),
                    *classification.lexical_terms,
                )
            )
        )[:5]
        for term in terms:
            for includes in self._scopes(request, plan):
                result = self._lexical.execute(
                    SearchTextRequest(
                        term,
                        includes,
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
                        match_line=hit.match_line,
                    )
                    for hit in result.data
                )
        state = CapabilityState.READY
        if not candidates and any(
            warning.code in {"ripgrep_unavailable", "embedding_unavailable"} for warning in warnings
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
        plan: QueryPlan,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        terms = tuple(
            dict.fromkeys(
                (
                    *plan.target_terms,
                    *((plan.anchor_name,) if plan.anchor_name else ()),
                    *classification.identifiers,
                )
            )
        )[:5]
        paths: tuple[str | None, ...] = (
            (plan.anchor_path, None) if plan.anchor_path is not None else (None,)
        )
        for term in terms:
            for path in paths:
                result = self._symbols.execute(
                    FindSymbolRequest(
                        term,
                        max_results=max(request.max_results * 2, 20),
                        exact=False,
                        path=path,
                    )
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
                    elif any(folded_term in name for name in names):
                        score = 0.85
                    else:
                        score = 0.75
                    candidates.append(
                        HybridCandidate(
                            CodeSnippet(
                                symbol.location,
                                item.content or "",
                                None,
                                item.file_hash,
                            ),
                            MatchType.SYMBOL,
                            score,
                            (term,),
                            "Structural symbol match.",
                            symbol.symbol_id,
                            symbol.name,
                            group_id=symbol.symbol_id,
                            symbol_kind=symbol.kind,
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
        plan: QueryPlan,
    ) -> _StrategyResult:
        started = perf_counter()
        candidates: list[HybridCandidate] = []
        warnings: list[ToolWarning] = []
        truncated = False
        terms = tuple(
            dict.fromkeys(
                (
                    *plan.target_terms,
                    *((plan.anchor_name,) if plan.anchor_name else ()),
                    *classification.identifiers,
                )
            )
        )[:5]
        for term in terms:
            for includes in self._scopes(request, plan):
                result = self._references.execute(
                    FindReferencesRequest(
                        term,
                        max(request.max_results * 2, 20),
                        includes,
                        request.exclude_globs,
                        request.timeout_seconds,
                        True,
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
                                item.content or "",
                                None,
                                item.file_hash,
                            ),
                            MatchType.REFERENCE,
                            (
                                0.30
                                if reference.kind == "comment_textual"
                                else (
                                    0.85
                                    if reference.kind
                                    not in {
                                        "textual",
                                        "unknown_textual",
                                        "configuration_textual",
                                    }
                                    else (
                                        0.45
                                        if reference.kind == "configuration_textual"
                                        else 0.70
                                    )
                                )
                            ),
                            (term,),
                            "Reference to a requested identifier.",
                            reference.reference_id,
                            reference.target_name,
                            comment_only=reference.kind == "comment_textual",
                            reference_kind=reference.kind,
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
        if not result.data and any(warning.code == "embedding_unavailable" for warning in warnings):
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
        classification: QueryClassification,
        candidates: list[HybridCandidate],
        plan: QueryPlan,
    ) -> tuple[tuple[HybridCandidate, ...], tuple[ToolWarning, ...]]:
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        outlines: dict[str, tuple[object, ...]] = {}
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
            snippet, snippet_truncated, source_location = _bounded_snippet(
                source,
                location,
                request,
                classification,
                plan,
            )
            reference_kind = candidate.reference_kind
            comment_only = candidate.comment_only
            if candidate.match_type in {
                MatchType.EXACT,
                MatchType.EXACT_LITERAL,
                MatchType.FULL_TEXT,
                MatchType.FTS_PHRASE,
                MatchType.FTS_TERM,
                MatchType.SUBSTRING,
                MatchType.REGEX,
            }:
                match_line = candidate.match_line or location.start_line
                candidate_terms = candidate.matched_terms or plan.query_terms
                for term in candidate_terms:
                    if not term:
                        continue
                    classified = _classify_lexical_reference(
                        location.path,
                        source.content,
                        match_line,
                        term,
                        candidate.snippet.content,
                    )
                    if classified == "comment_textual":
                        reference_kind = classified
                        comment_only = True
                        break
                    if classified == "configuration_textual":
                        reference_kind = classified
            group_id = candidate.group_id
            source_name = candidate.source_name
            symbol_kind = candidate.symbol_kind
            if (
                group_id is None
                and candidate.match_type is not MatchType.PATH
                and self._store is not None
                and self._project is not None
            ):
                if location.path not in outlines:
                    try:
                        outlines[location.path] = self._store.get_outline(
                            self._project.project_id,
                            location.path,
                        )
                    except CodeHarnessError:
                        outlines[location.path] = ()
                enclosing = []
                for result in outlines[location.path]:
                    symbol = getattr(result, "symbol", None)
                    if (
                        symbol is not None
                        and symbol.location.start_line <= location.start_line
                        and symbol.location.end_line >= location.end_line
                    ):
                        enclosing.append(symbol)
                if enclosing:
                    selected_symbol = min(
                        enclosing,
                        key=lambda symbol: (
                            symbol.location.end_line - symbol.location.start_line,
                            symbol.location.start_line,
                            symbol.symbol_id,
                        ),
                    )
                    group_id = selected_symbol.symbol_id
                    source_name = source_name or selected_symbol.name
                    symbol_kind = symbol_kind or selected_symbol.kind
            if group_id is None:
                group_id = (
                    f"path:{location.path}"
                    if candidate.match_type is MatchType.PATH
                    else (
                        f"location:{location.path}:"
                        f"{location.start_line}:{location.end_line}"
                    )
                )
            valid.append(
                replace(
                    candidate,
                    snippet=snippet,
                    snippet_truncated=snippet_truncated,
                    source_location=source_location,
                    group_id=group_id,
                    source_name=source_name,
                    symbol_kind=symbol_kind,
                    reference_kind=reference_kind,
                    comment_only=comment_only,
                    scope=(
                        "anchor"
                        if plan.anchor_path is not None
                        and location.path == plan.anchor_path
                        else "fallback"
                    ),
                )
            )
        return tuple(valid), normalize_warnings(warnings)

    @staticmethod
    def _attach_path_evidence(
        candidates: tuple[HybridCandidate, ...],
    ) -> tuple[HybridCandidate, ...]:
        code = tuple(
            candidate for candidate in candidates if candidate.match_type is not MatchType.PATH
        )
        paths = tuple(
            candidate for candidate in candidates if candidate.match_type is MatchType.PATH
        )
        if not code:
            return paths
        attached = list(code)
        seen: set[tuple[str, str]] = set()
        for path_candidate in paths:
            for code_candidate in code:
                if (
                    path_candidate.snippet.location.path
                    != code_candidate.snippet.location.path
                    or code_candidate.group_id is None
                ):
                    continue
                key = (path_candidate.snippet.location.path, code_candidate.group_id)
                if key in seen:
                    continue
                seen.add(key)
                attached.append(
                    replace(
                        path_candidate,
                        snippet=code_candidate.snippet,
                        group_id=code_candidate.group_id,
                        scope=code_candidate.scope,
                        snippet_truncated=False,
                        source_location=None,
                    )
                )
        return tuple(attached)

    def _materialize(
        self,
        hits: tuple[HybridSearchHit, ...],
        request: SearchCodeRequest,
        classification: QueryClassification,
        plan: QueryPlan,
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
            snippet, snippet_truncated, source_location = _bounded_snippet(
                source,
                location,
                request,
                classification,
                plan,
            )
            if hit.source_location is not None:
                source_location = hit.source_location
                snippet_truncated = True
            materialized.append(
                replace(
                    hit,
                    snippet=snippet,
                    matched_terms=tuple(
                        term
                        for term in plan.query_terms
                        if term.casefold() in snippet.content.casefold()
                    ),
                    snippet_truncated=hit.snippet_truncated or snippet_truncated,
                    source_location=source_location,
                )
            )
        return tuple(materialized), normalize_warnings(warnings)
