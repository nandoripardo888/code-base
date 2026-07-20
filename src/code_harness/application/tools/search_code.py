from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from fnmatch import fnmatchcase

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
from code_harness.application.tools.search_files import SearchFilesTool
from code_harness.application.tools.search_text import SearchTextTool
from code_harness.application.tools.semantic_search import SemanticSearchTool
from code_harness.application.tools.structural import FindReferencesTool, FindSymbolTool
from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import HybridSearchHit, QueryClassification
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.index_source_reader import IndexSourceReader

_StrategyResult = tuple[list[HybridCandidate], list[str], bool]


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
        classifier: QueryClassifier | None = None,
        ranker: HybridRanker | None = None,
    ) -> None:
        self._lexical = lexical
        self._symbols = symbols
        self._references = references
        self._semantic = semantic
        self._paths = paths
        self._reader = reader
        self._classifier = classifier or QueryClassifier()
        self._ranker = ranker or HybridRanker()

    def execute(self, request: SearchCodeRequest) -> ToolResult[tuple[HybridSearchHit, ...]]:
        classification = self._classifier.classify(request.query)

        def search() -> tuple[tuple[HybridSearchHit, ...], tuple[str, ...], bool]:
            tasks: dict[str, Callable[[], _StrategyResult]] = {
                "lexical": lambda: self._lexical_candidates(request, classification),
                "symbols": lambda: self._symbol_candidates(request, classification),
                "references": lambda: self._reference_candidates(request, classification),
                "paths": lambda: self._path_candidates(request, classification),
            }
            if classification.kind is not QueryKind.EXACT:
                tasks["semantic"] = lambda: self._semantic_candidates(request)

            candidates: list[HybridCandidate] = []
            warnings: list[str] = []
            truncated = False
            with ThreadPoolExecutor(
                max_workers=len(tasks),
                thread_name_prefix="hybrid-search",
            ) as pool:
                futures = {pool.submit(operation): name for name, operation in tasks.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        found, strategy_warnings, strategy_truncated = future.result()
                    except CodeHarnessError as error:
                        warnings.append(
                            f"Hybrid {name} strategy is unavailable ({error.code.value}): "
                            f"{error.message}"
                        )
                        continue
                    candidates.extend(found)
                    warnings.extend(strategy_warnings)
                    truncated = truncated or strategy_truncated

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
                tuple(dict.fromkeys(warnings)),
                truncated or len(valid) > len(materialized),
            )

        result, elapsed_ms = timed(search)
        hits, warnings, truncated = result
        return ToolResult(hits, elapsed_ms, truncated=truncated, warnings=warnings)

    def _lexical_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        candidates: list[HybridCandidate] = []
        warnings: list[str] = []
        truncated = False
        per_term = max(request.max_results * 2, 20)
        for term in classification.lexical_terms[:3]:
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
            warnings.extend(result.warnings)
            truncated = truncated or result.truncated
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
        return candidates, warnings, truncated

    def _symbol_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        candidates: list[HybridCandidate] = []
        warnings: list[str] = []
        truncated = False
        for term in classification.identifiers[:3]:
            result = self._symbols.execute(
                FindSymbolRequest(term, max(request.max_results * 2, 20), False)
            )
            warnings.extend(result.warnings)
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
        return candidates, warnings, truncated

    def _reference_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
        candidates: list[HybridCandidate] = []
        warnings: list[str] = []
        truncated = False
        for term in classification.identifiers[:3]:
            result = self._references.execute(
                FindReferencesRequest(
                    term,
                    max(request.max_results * 2, 20),
                    request.include_globs,
                    request.exclude_globs,
                    request.timeout_seconds,
                )
            )
            warnings.extend(result.warnings)
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
                        0.85 if reference.kind != "textual" else 0.70,
                        (term,),
                        "Reference to a requested identifier.",
                        reference.reference_id,
                        reference.target_name,
                    )
                )
        return candidates, warnings, truncated

    def _semantic_candidates(self, request: SearchCodeRequest) -> _StrategyResult:
        result = self._semantic.execute(
            SemanticSearchRequest(
                request.query,
                request.include_globs,
                request.exclude_globs,
                request.languages,
                max(request.max_results * 2, 20),
            )
        )
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
            list(result.warnings),
            result.truncated,
        )

    def _path_candidates(
        self,
        request: SearchCodeRequest,
        classification: QueryClassification,
    ) -> _StrategyResult:
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
        return candidates, [], truncated

    def _validate_candidates(
        self,
        request: SearchCodeRequest,
        candidates: list[HybridCandidate],
    ) -> tuple[tuple[HybridCandidate, ...], tuple[str, ...]]:
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        valid: list[HybridCandidate] = []
        warnings: list[str] = []
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
                    f"Skipped {location.path}: current file is unavailable ({source.code.value})."
                )
                continue
            if languages and (source.language or "").casefold() not in languages:
                continue
            if source.content_hash != candidate.snippet.file_hash:
                warnings.append(f"Skipped stale hybrid result for {location.path}; reindex it.")
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
        return tuple(valid), tuple(dict.fromkeys(warnings))

    def _materialize(
        self,
        hits: tuple[HybridSearchHit, ...],
    ) -> tuple[tuple[HybridSearchHit, ...], tuple[str, ...]]:
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        materialized: list[HybridSearchHit] = []
        warnings: list[str] = []
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
                    f"Skipped {location.path}: current file is unavailable ({source.code.value})."
                )
                continue
            if source.content_hash != hit.snippet.file_hash:
                warnings.append(f"Skipped stale hybrid result for {location.path}; reindex it.")
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
        return tuple(materialized), tuple(dict.fromkeys(warnings))
