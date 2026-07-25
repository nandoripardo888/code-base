import re
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from pathlib import PurePosixPath
from time import perf_counter

from code_harness.application.dto.requests import (
    FindDefinitionRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetFileOutlineRequest,
)
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import CapabilityState, IndexState
from code_harness.domain.errors import CodeHarnessError, is_recoverable_error
from code_harness.domain.models.capability import StrategyOutcome, ToolWarning
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.result_truncation import (
    ResultTruncation,
    TruncationReason,
    merge_truncations,
    truncation,
)
from code_harness.domain.models.structural import CodeReference, CodeSymbol, StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.text_searcher import TextSearcher

_HASH_COMMENT_SUFFIXES = {
    ".ps1",
    ".py",
    ".r",
    ".rb",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
_DASH_COMMENT_SUFFIXES = {".hs", ".lua", ".pck", ".pkg", ".sql"}
_C_LINE_COMMENT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
}
_CONFIGURATION_SUFFIXES = {
    ".cfg",
    ".conf",
    ".dtm",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
_REFERENCE_KIND_PRIORITY = {
    "definition": 0,
    "instantiation": 1,
    "call": 2,
    "type_use": 3,
    "import": 4,
    "configuration_textual": 5,
    "unknown_textual": 6,
    "comment_textual": 7,
}


def _comment_syntax(path: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix in _C_LINE_COMMENT_SUFFIXES:
        return ("//",), (("/*", "*/"),)
    if suffix in _HASH_COMMENT_SUFFIXES:
        return ("#",), ()
    if suffix in _DASH_COMMENT_SUFFIXES:
        return ("--",), (("/*", "*/"),)
    if suffix in {".html", ".htm", ".xml"}:
        return (), (("<!--", "-->"),)
    return (), ()


def _comment_mask(path: str, content: str, line_number: int) -> tuple[bool, ...]:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ()
    line_markers, block_pairs = _comment_syntax(path)
    block_end: str | None = None
    requested: tuple[bool, ...] = ()
    for current_line, line in enumerate(lines, start=1):
        mask = [False] * len(line)
        quote: str | None = None
        escaped = False
        index = 0
        while index < len(line):
            if block_end is not None:
                end = line.find(block_end, index)
                if end < 0:
                    for position in range(index, len(line)):
                        mask[position] = True
                    index = len(line)
                    continue
                for position in range(index, min(len(line), end + len(block_end))):
                    mask[position] = True
                index = end + len(block_end)
                block_end = None
                continue
            if quote is not None:
                if escaped:
                    escaped = False
                elif line[index] == "\\":
                    escaped = True
                elif line[index] == quote:
                    quote = None
                index += 1
                continue
            if line[index] in {"'", '"', "`"}:
                quote = line[index]
                index += 1
                continue
            marker = next(
                (value for value in line_markers if line.startswith(value, index)),
                None,
            )
            if marker is not None:
                for position in range(index, len(line)):
                    mask[position] = True
                break
            pair = next(
                (value for value in block_pairs if line.startswith(value[0], index)),
                None,
            )
            if pair is not None:
                block_end = pair[1]
                continue
            index += 1
        if current_line == line_number:
            requested = tuple(mask)
            break
    return requested


def _is_confirmed_comment(
    path: str,
    source_content: str | None,
    line_number: int,
    target: str,
) -> bool:
    if not source_content or not target:
        return False
    lines = source_content.splitlines()
    if not 1 <= line_number <= len(lines):
        return False
    line = lines[line_number - 1]
    mask = _comment_mask(path, source_content, line_number)
    folded = line.casefold()
    needle = target.casefold()
    positions: list[int] = []
    start = 0
    while True:
        found = folded.find(needle, start)
        if found < 0:
            break
        positions.append(found)
        start = found + max(1, len(needle))
    return bool(positions) and all(
        position < len(mask) and mask[position] for position in positions
    )


def _classify_lexical_reference(
    path: str,
    source_content: str | None,
    line_number: int,
    target: str,
    fallback_content: str,
) -> str:
    if _is_confirmed_comment(path, source_content, line_number, target):
        return "comment_textual"
    lines = source_content.splitlines() if source_content else fallback_content.splitlines()
    line = (
        lines[line_number - 1]
        if source_content and 1 <= line_number <= len(lines)
        else fallback_content
    )
    code_chars = list(line)
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote is not None:
            code_chars[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"', "`"}:
            quote = character
            code_chars[index] = " "
    code_line = "".join(code_chars)
    escaped_target = re.escape(target)
    if re.search(rf"(?i)^\s*(?:import|from|using)\b.*\b{escaped_target}\b", code_line):
        return "import"
    if re.search(rf"(?i)\bnew\s+{escaped_target}\b", code_line):
        return "instantiation"
    if re.search(rf"(?i)\b{escaped_target}\s*\(", code_line):
        return "call"
    if re.search(
        rf"(?i)(?:^|[\s<>,?&|]){escaped_target}(?:\[\])?\s+[A-Za-z_$][\w$]*",
        code_line,
    ):
        return "type_use"
    if PurePosixPath(path).suffix.casefold() in _CONFIGURATION_SUFFIXES:
        return "configuration_textual"
    return "unknown_textual"


def _warning_from_error(error: CodeHarnessError, *, message: str | None = None) -> ToolWarning:
    return ToolWarning(
        code=error.code.value,
        message=message or error.message,
        recoverable=error.recoverable,
        capability=error.capability,
        remediation=error.remediation,
    )


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
        *,
        include_content: bool = True,
        max_content_chars: int | None = None,
        max_symbols: int | None = None,
        max_depth: int | None = None,
        symbol_kinds: tuple[str, ...] = (),
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        status = self._store.get_status(self._project)
        if (
            status.state not in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
            or not status.structural_schema_ready
        ):
            return ToolResult(
                (),
                0,
                warnings=(
                    ToolWarning(
                        code="structural_index_not_ready",
                        message="Structural index is not ready; run index first.",
                        recoverable=True,
                        capability="structural",
                        remediation="Run index_project before structural tools.",
                    ),
                ),
                index_state=status.state.value,
            )
        results, elapsed_ms = timed(operation)
        filtered = self._filter_symbols(
            results,
            max_symbols=max_symbols,
            max_depth=max_depth,
            symbol_kinds=symbol_kinds,
        )
        validated, warnings = self._validate(
            filtered,
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
        return ToolResult(
            validated,
            elapsed_ms,
            truncated=len(validated) < len(results),
            warnings=normalize_warnings(warnings),
            index_state=status.state.value,
        )

    def _filter_symbols(
        self,
        results: tuple[StructuralSearchResult, ...],
        *,
        max_symbols: int | None,
        max_depth: int | None,
        symbol_kinds: tuple[str, ...],
    ) -> tuple[StructuralSearchResult, ...]:
        filtered: list[StructuralSearchResult] = []
        parents = {
            item.symbol.symbol_id: item.symbol.parent_symbol_id
            for item in results
            if item.symbol is not None
        }
        for result in results:
            symbol = result.symbol
            if symbol is None:
                filtered.append(result)
                continue
            if symbol_kinds and symbol.kind not in symbol_kinds:
                continue
            if max_depth is not None:
                depth = 0
                parent_id = symbol.parent_symbol_id
                while parent_id is not None:
                    depth += 1
                    if depth > max_depth:
                        break
                    parent_id = parents.get(parent_id)
                if depth > max_depth:
                    continue
            filtered.append(result)
            if max_symbols is not None and len(filtered) >= max_symbols:
                break
        return tuple(filtered)

    def _validate(
        self,
        results: tuple[StructuralSearchResult, ...],
        *,
        require_target_name: bool = False,
        include_content: bool = True,
        max_content_chars: int | None = None,
    ) -> tuple[tuple[StructuralSearchResult, ...], tuple[str | ToolWarning, ...]]:
        sources: dict[str, IndexedSource | None] = {}
        valid: list[StructuralSearchResult] = []
        warnings: list[str | ToolWarning] = []
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
                        ToolWarning(
                            code=error.code.value,
                            message=(
                                f"Skipped {location.path}: current file is unavailable "
                                f"({error.code.value})."
                            ),
                            recoverable=True,
                            capability="filesystem",
                            remediation=error.remediation,
                        )
                    )
            source = sources[location.path]
            if source is None:
                continue
            if source.content_hash != result.file_hash:
                warnings.append(
                    ToolWarning(
                        code="stale_structural_result",
                        message=(
                            f"Skipped stale structural result for {location.path}; reindex it."
                        ),
                        recoverable=True,
                        capability="structural",
                        remediation="Run index_project to refresh the structural index.",
                    )
                )
                continue
            lines = source.content.splitlines(keepends=True)
            content = "".join(lines[location.start_line - 1 : location.end_line])
            if (
                require_target_name
                and result.reference is not None
                and result.reference.target_name.casefold() not in content.casefold()
            ):
                warnings.append(
                    ToolWarning(
                        code="invalid_reference_range",
                        message=(
                            f"Skipped outdated reference for {location.path}:"
                            f"{location.start_line}; target no longer present."
                        ),
                        recoverable=True,
                        capability="structural",
                        remediation="Run index_project to refresh references.",
                    )
                )
                continue
            if include_content:
                if max_content_chars is not None and len(content) > max_content_chars:
                    content = content[:max_content_chars]
                result_content: str | None = content
                content_included = True
            else:
                result_content = None
                content_included = False
            if result.reference is not None:
                reference = replace(result.reference, validated=True)
                valid.append(
                    replace(
                        result,
                        reference=reference,
                        content=result_content,
                        content_included=content_included,
                    )
                )
            else:
                valid.append(
                    replace(
                        result,
                        content=result_content,
                        content_included=content_included,
                    )
                )
        return tuple(valid), tuple(dict.fromkeys(warnings))


class GetFileOutlineTool(_StructuralTool):
    def execute(
        self, request: GetFileOutlineRequest
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        self._reader.load(request.path)
        return self._execute(
            lambda: self._store.get_outline(self._project.project_id, request.path),
            include_content=request.effective_include_content,
            max_content_chars=request.max_content_chars_per_symbol,
            max_symbols=request.max_symbols,
            max_depth=request.max_depth,
            symbol_kinds=request.symbol_kinds,
        )


class FindSymbolTool(_StructuralTool):
    def execute(self, request: FindSymbolRequest) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        def operation() -> tuple[StructuralSearchResult, ...]:
            results = self._store.find_symbols(
                self._project.project_id,
                request.query,
                exact=request.exact,
                limit=max(request.max_results * 4, request.max_results),
            )
            filtered: list[StructuralSearchResult] = []
            for item in results:
                symbol = item.symbol
                if symbol is None:
                    continue
                if request.kind and symbol.kind.casefold() != request.kind.casefold():
                    continue
                if request.path:
                    path = symbol.location.path.replace("\\", "/")
                    needle = request.path.replace("\\", "/")
                    if path != needle and not path.endswith(f"/{needle.lstrip('/')}"):
                        continue
                if request.language:
                    try:
                        source = self._reader.load(symbol.location.path)
                    except CodeHarnessError:
                        continue
                    if (source.language or "").casefold() != request.language.casefold():
                        continue
                if request.parameter_count is not None:
                    signature = symbol.canonical_signature or symbol.signature or ""
                    params = _parameter_count(signature)
                    if params != request.parameter_count:
                        continue
                filtered.append(item)
                if len(filtered) >= request.max_results:
                    break
            return tuple(filtered)

        return self._execute(
            operation,
            include_content=request.effective_include_content,
            max_content_chars=request.max_content_chars_per_symbol,
        )


def _parameter_count(signature: str) -> int:
    start = signature.find("(")
    end = signature.rfind(")")
    if start < 0 or end <= start:
        return 0
    inside = signature[start + 1 : end].strip()
    if not inside:
        return 0
    depth = 0
    count = 1
    for char in inside:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            count += 1
    return count


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
        warnings: list[str | ToolWarning] = []
        strategies: list[StrategyOutcome] = []
        upstream_truncations: list[ResultTruncation] = []
        structural_ready = (
            status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
            and status.structural_schema_ready
        )
        target_symbol = None
        simple_name = request.query.rsplit(".", 1)[-1]
        if structural_ready:
            target_symbol, simple_name = self._resolve_target(request.query)

        def search() -> tuple[StructuralSearchResult, ...]:
            nonlocal warnings
            structural_hits: tuple[StructuralSearchResult, ...] = ()
            structural_started = perf_counter()
            if structural_ready:
                try:
                    raw = self._store.find_references(
                        self._project.project_id,
                        simple_name,
                        limit=request.max_results + 1,
                    )
                    structural_hits, validation_warnings = self._validate(
                        raw, require_target_name=True, include_content=True
                    )
                    structural_hits = self._scope_references(
                        structural_hits, target_symbol, simple_name
                    )
                    warnings.extend(validation_warnings)
                    strategies.append(
                        StrategyOutcome(
                            strategy="structural",
                            state=CapabilityState.READY,
                            hit_count=len(structural_hits),
                            elapsed_ms=int((perf_counter() - structural_started) * 1000),
                        )
                    )
                except CodeHarnessError as error:
                    if not is_recoverable_error(error):
                        raise
                    warning = _warning_from_error(
                        error,
                        message=(
                            "Structural references unavailable; continuing with lexical search."
                        ),
                    )
                    warnings.append(warning)
                    strategies.append(
                        StrategyOutcome(
                            strategy="structural",
                            state=CapabilityState.UNAVAILABLE,
                            elapsed_ms=int((perf_counter() - structural_started) * 1000),
                            warning=warning,
                            error_code=error.code.value,
                        )
                    )
            else:
                warning = ToolWarning(
                    code="structural_references_unavailable",
                    message=("Structural index is not ready; returned lexical references only."),
                    recoverable=True,
                    capability="structural",
                    remediation="Run index_project before structural reference lookup.",
                )
                warnings.append(warning)
                strategies.append(
                    StrategyOutcome(
                        strategy="structural",
                        state=CapabilityState.UNAVAILABLE,
                        elapsed_ms=0,
                        warning=warning,
                        error_code="structural_references_unavailable",
                    )
                )

            lexical_hits: list[StructuralSearchResult] = []
            lexical_started = perf_counter()
            try:
                lexical_limit = min(10_000, max(32, request.max_results + 1))
                lexical = None
                source_cache: dict[str, IndexedSource | CodeHarnessError] = {}
                while True:
                    lexical = self._lexical_searcher.search(
                        query=simple_name,
                        regex=False,
                        include_globs=request.include_globs,
                        exclude_globs=request.exclude_globs,
                        case_sensitive=False,
                        max_results=lexical_limit,
                        context_lines=0,
                        timeout_seconds=request.timeout_seconds,
                    )
                    warnings.extend(normalize_warnings(lexical.warnings))
                    classified_non_comments = 0
                    for hit in lexical.hits:
                        path = hit.snippet.location.path
                        if path not in source_cache:
                            try:
                                source_cache[path] = self._reader.load(path)
                            except CodeHarnessError as error:
                                source_cache[path] = error
                        source = source_cache[path]
                        source_content = (
                            None if isinstance(source, CodeHarnessError) else source.content
                        )
                        kind = _classify_lexical_reference(
                            path,
                            source_content,
                            hit.snippet.location.start_line,
                            simple_name,
                            hit.snippet.content,
                        )
                        if kind != "comment_textual":
                            classified_non_comments += 1
                    if (
                        not lexical.truncated
                        or classified_non_comments > request.max_results
                        or lexical_limit >= 10_000
                    ):
                        break
                    lexical_limit = min(10_000, lexical_limit * 2)

                assert lexical is not None
                if lexical.truncated:
                    upstream_truncations.append(
                        truncation(TruncationReason.CANDIDATE_LIMIT, candidates=True)
                    )
                for hit in lexical.hits:
                    location = hit.snippet.location
                    cached_source = source_cache.get(location.path)
                    source_content = (
                        None
                        if cached_source is None
                        or isinstance(cached_source, CodeHarnessError)
                        else cached_source.content
                    )
                    kind = _classify_lexical_reference(
                        location.path,
                        source_content,
                        location.start_line,
                        simple_name,
                        hit.snippet.content,
                    )
                    if kind == "comment_textual" and not request.include_comments:
                        continue
                    reference_id = sha256(
                        (
                            f"{location.path}\x1f{location.start_line}\x1f"
                            f"{location.start_column}\x1f{simple_name}\x1f{kind}"
                        ).encode()
                    ).hexdigest()[:32]
                    lexical_hits.append(
                        StructuralSearchResult(
                            None,
                            CodeReference(
                                reference_id,
                                simple_name,
                                kind,
                                location,
                                source="lexical",
                                confidence=(
                                    0.25
                                    if kind == "comment_textual"
                                    else (
                                        0.45
                                        if kind == "configuration_textual"
                                        else 0.65
                                    )
                                ),
                                validated=True,
                                resolution="name_only",
                            ),
                            hit.snippet.content,
                            hit.snippet.file_hash,
                            content_included=True,
                        )
                    )
                lexical_hits = list(
                    self._scope_references(tuple(lexical_hits), target_symbol, simple_name)
                )
                strategies.append(
                    StrategyOutcome(
                        strategy="ripgrep",
                        state=CapabilityState.READY,
                        hit_count=len(lexical_hits),
                        elapsed_ms=int((perf_counter() - lexical_started) * 1000),
                    )
                )
            except CodeHarnessError as error:
                if not is_recoverable_error(error):
                    raise
                warning = _warning_from_error(
                    error,
                    message=("Lexical reference search skipped because Ripgrep is unavailable."),
                )
                warnings.append(
                    ToolWarning(
                        code="lexical_reference_search_skipped",
                        message=warning.message,
                        recoverable=True,
                        capability=warning.capability,
                        remediation=warning.remediation,
                    )
                )
                warnings.append(warning)
                strategies.append(
                    StrategyOutcome(
                        strategy="ripgrep",
                        state=CapabilityState.UNAVAILABLE,
                        elapsed_ms=int((perf_counter() - lexical_started) * 1000),
                        warning=warning,
                        error_code=error.code.value,
                    )
                )

            lexical_hits.sort(
                key=lambda item: (
                    _REFERENCE_KIND_PRIORITY.get(
                        item.reference.kind if item.reference is not None else "",
                        6,
                    ),
                    item.reference.location.path if item.reference is not None else "",
                    item.reference.location.start_line if item.reference is not None else 0,
                )
            )
            ordered_hits = sorted(
                (*structural_hits, *lexical_hits),
                key=lambda item: (
                    item.reference is None or item.reference.source != "structural",
                    _REFERENCE_KIND_PRIORITY.get(
                        item.reference.kind if item.reference is not None else "",
                        6,
                    ),
                    item.reference.location.path if item.reference is not None else "",
                    item.reference.location.start_line if item.reference is not None else 0,
                    item.reference.location.start_column
                    if item.reference is not None
                    and item.reference.location.start_column is not None
                    else 0,
                ),
            )
            combined: list[StructuralSearchResult] = []
            seen: set[tuple[str, int, int]] = set()
            for candidate in ordered_hits:
                if candidate.reference is None:
                    continue
                key = (
                    candidate.reference.location.path,
                    candidate.reference.location.start_line,
                    candidate.reference.location.end_line,
                )
                if key in seen:
                    continue
                seen.add(key)
                combined.append(candidate)
            return tuple(combined)

        results, elapsed_ms = timed(search)
        if (
            not results
            and strategies
            and all(outcome.state is CapabilityState.UNAVAILABLE for outcome in strategies)
        ):
            raise _both_unavailable_error(strategies)

        has_extra = len(results) > request.max_results
        result_truncation = merge_truncations(
            *upstream_truncations,
            (truncation(TruncationReason.RESULT_LIMIT, results=True) if has_extra else None),
        )
        return ToolResult(
            results[: request.max_results],
            elapsed_ms,
            truncated=result_truncation is not None,
            truncation=result_truncation,
            warnings=normalize_warnings(warnings),
            index_state=status.state.value,
            strategies=tuple(strategies),
        )

    def _resolve_target(self, query: str) -> tuple[CodeSymbol | None, str]:
        simple_name = query.rsplit(".", 1)[-1]
        try:
            matches = self._store.find_symbols(
                self._project.project_id,
                query,
                exact=True,
                limit=50,
            )
        except CodeHarnessError:
            return None, simple_name

        symbols = [item.symbol for item in matches if item.symbol is not None]
        folded = query.casefold()
        qualified = [
            symbol for symbol in symbols if (symbol.qualified_name or "").casefold() == folded
        ]
        if qualified:
            return qualified[0], qualified[0].name
        named = [symbol for symbol in symbols if symbol.name.casefold() == folded]
        if len(named) == 1:
            return named[0], named[0].name
        if len(symbols) == 1:
            return symbols[0], symbols[0].name
        return None, simple_name

    def _owner_symbol(self, target_symbol: CodeSymbol) -> CodeSymbol:
        if target_symbol.parent_symbol_id:
            try:
                parents = self._store.find_symbols_by_ids(
                    self._project.project_id,
                    (target_symbol.parent_symbol_id,),
                )
            except CodeHarnessError:
                parents = ()
            if parents and parents[0].symbol is not None:
                return parents[0].symbol
        # Fallback: nearest enclosing type on the same path.
        try:
            outline = self._store.get_outline(self._project.project_id, target_symbol.location.path)
        except CodeHarnessError:
            return target_symbol
        enclosing = None
        for item in outline:
            symbol = item.symbol
            if symbol is None or symbol.kind not in {
                "class",
                "interface",
                "enum",
                "record",
                "module",
                "package",
            }:
                continue
            if (
                symbol.location.start_line
                <= target_symbol.location.start_line
                <= symbol.location.end_line
            ) and (
                enclosing is None
                or (
                    symbol.location.start_line >= enclosing.location.start_line
                    and symbol.location.end_line <= enclosing.location.end_line
                )
            ):
                enclosing = symbol
        return enclosing or target_symbol

    def _symbols_named(self, simple_name: str) -> tuple[CodeSymbol, ...]:
        try:
            matches = self._store.find_symbols(
                self._project.project_id,
                simple_name,
                exact=True,
                limit=100,
            )
        except CodeHarnessError:
            return ()
        return tuple(
            item.symbol
            for item in matches
            if item.symbol is not None and item.symbol.name.casefold() == simple_name.casefold()
        )

    def _annotate_references(
        self,
        results: tuple[StructuralSearchResult, ...],
        target_symbol: CodeSymbol | None,
        simple_name: str,
        owner_symbol: CodeSymbol | None = None,
        *,
        private_target: bool = False,
        homonym_owners: tuple[CodeSymbol, ...] = (),
    ) -> tuple[StructuralSearchResult, ...]:
        annotated: list[StructuralSearchResult] = []
        named_symbols = () if target_symbol is not None else self._symbols_named(simple_name)
        for result in results:
            reference = result.reference
            if reference is None:
                continue
            kind = reference.kind
            target_id = None
            resolution = "name_only"
            confidence = min(reference.confidence, 0.85)

            if target_symbol is not None:
                if _is_symbol_definition_line(reference.location, target_symbol):
                    kind = "definition"
                    target_id = target_symbol.symbol_id
                    resolution = "symbol_id"
                    confidence = 1.0
                elif owner_symbol is not None and _location_in_symbol(
                    reference.location, owner_symbol
                ):
                    target_id = target_symbol.symbol_id
                    resolution = "symbol_id"
                    confidence = 1.0
                elif private_target or any(
                    _location_in_symbol(reference.location, other) for other in homonym_owners
                ):
                    continue
                else:
                    # Possible external call to a non-private method.
                    kind = reference.kind
                    target_id = None
                    resolution = "name_only"
                    confidence = min(reference.confidence, 0.7)
            else:
                matching_defs = [
                    symbol
                    for symbol in named_symbols
                    if _is_symbol_definition_line(reference.location, symbol)
                ]
                if matching_defs:
                    kind = "definition"
                    if len(matching_defs) == 1:
                        target_id = matching_defs[0].symbol_id
                        resolution = "symbol_id"
                        confidence = 1.0

            updated = replace(
                reference,
                target_name=simple_name,
                kind=kind,
                target_symbol_id=target_id,
                resolution=resolution,
                confidence=confidence,
            )
            annotated.append(replace(result, reference=updated))
        return tuple(annotated)

    def _scope_references(
        self,
        results: tuple[StructuralSearchResult, ...],
        target_symbol: CodeSymbol | None,
        simple_name: str,
    ) -> tuple[StructuralSearchResult, ...]:
        named_symbols = self._symbols_named(simple_name)
        if target_symbol is None:
            # Simple-name query: classify definitions, keep other hits as name_only.
            annotated: list[StructuralSearchResult] = []
            for result in results:
                reference = result.reference
                if reference is None:
                    continue
                matching_defs = [
                    symbol
                    for symbol in named_symbols
                    if _is_symbol_definition_line(reference.location, symbol)
                ]
                if matching_defs:
                    target_id = matching_defs[0].symbol_id if len(matching_defs) == 1 else None
                    annotated.append(
                        replace(
                            result,
                            reference=replace(
                                reference,
                                kind="definition",
                                target_symbol_id=target_id,
                                resolution="symbol_id" if target_id else "name_only",
                                confidence=1.0 if target_id else 0.75,
                            ),
                        )
                    )
                else:
                    annotated.append(
                        replace(
                            result,
                            reference=replace(
                                reference,
                                target_symbol_id=None,
                                resolution="name_only",
                                confidence=min(reference.confidence, 0.85),
                            ),
                        )
                    )
            return tuple(annotated)

        owner = self._owner_symbol(target_symbol)
        private_target = _looks_private(target_symbol)
        homonym_owners = []
        for symbol in named_symbols:
            if symbol.symbol_id == target_symbol.symbol_id:
                continue
            homonym_owners.append(self._owner_symbol(symbol))

        return self._annotate_references(
            results,
            target_symbol,
            simple_name,
            owner,
            private_target=private_target,
            homonym_owners=tuple(homonym_owners),
        )


def _looks_private(symbol: CodeSymbol) -> bool:
    header = (symbol.signature or symbol.canonical_signature or "").split("(", 1)[0]
    return "private" in header.casefold().split()


def _location_in_symbol(location: CodeLocation, symbol: CodeSymbol) -> bool:
    return bool(
        location.path == symbol.location.path
        and symbol.location.start_line <= location.start_line <= symbol.location.end_line
    )


def _is_symbol_definition_line(location: CodeLocation, symbol: CodeSymbol) -> bool:
    return bool(
        location.path == symbol.location.path and location.start_line == symbol.location.start_line
    )


def _both_unavailable_error(strategies: list[StrategyOutcome]) -> CodeHarnessError:
    from code_harness.domain.enums import ErrorCode

    codes = [outcome.error_code for outcome in strategies if outcome.error_code]
    primary = next(
        (code for code in codes if code == "ripgrep_unavailable"),
        codes[0] if codes else "ripgrep_unavailable",
    )
    try:
        error_code = ErrorCode(primary)
    except ValueError:
        error_code = ErrorCode.RIPGREP_UNAVAILABLE
    return CodeHarnessError(
        error_code,
        "No reference strategies were available.",
        details={"strategies": [outcome.strategy for outcome in strategies]},
        recoverable=True,
        capability="structural",
        remediation="Run index_project and ensure Ripgrep is installed.",
    )
