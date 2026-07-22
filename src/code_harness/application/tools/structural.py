from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
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
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import CodeReference, StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.text_searcher import TextSearcher


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
            if require_target_name and result.reference is not None:
                if result.reference.target_name.casefold() not in content.casefold():
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
                        limit=request.max_results,
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
                        message="Structural references unavailable; continuing with lexical search.",
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
                    message=(
                        "Structural index is not ready; returned lexical references only."
                    ),
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
                lexical = self._lexical_searcher.search(
                    query=simple_name,
                    regex=False,
                    include_globs=request.include_globs,
                    exclude_globs=request.exclude_globs,
                    case_sensitive=False,
                    max_results=request.max_results,
                    context_lines=0,
                    timeout_seconds=request.timeout_seconds,
                )
                # Lexical searcher may return string warnings; normalize later.
                for warning in lexical.warnings:
                    if isinstance(warning, ToolWarning):
                        warnings.append(warning)
                    else:
                        warnings.append(
                            ToolWarning(
                                code="lexical_search_warning",
                                message=str(warning),
                                recoverable=True,
                                capability="ripgrep",
                            )
                        )
                for hit in lexical.hits:
                    location = hit.snippet.location
                    reference_id = sha256(
                        f"{location.path}\x1f{location.start_line}\x1f{simple_name}".encode()
                    ).hexdigest()[:32]
                    lexical_hits.append(
                        StructuralSearchResult(
                            None,
                            CodeReference(
                                reference_id,
                                simple_name,
                                "unknown_textual",
                                location,
                                source="lexical",
                                confidence=0.6,
                                validated=True,
                                resolution="name_only",
                            ),
                            hit.snippet.content,
                            hit.snippet.file_hash,
                            content_included=True,
                        )
                    )
                lexical_hits = list(
                    self._scope_references(
                        tuple(lexical_hits), target_symbol, simple_name
                    )
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
                    message=(
                        "Lexical reference search skipped because Ripgrep is unavailable."
                    ),
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

            combined: list[StructuralSearchResult] = list(structural_hits)
            seen = {
                (
                    item.reference.location.path,
                    item.reference.location.start_line,
                )
                for item in structural_hits
                if item.reference is not None
            }
            for hit in lexical_hits:
                assert hit.reference is not None
                key = (hit.reference.location.path, hit.reference.location.start_line)
                if key in seen:
                    continue
                seen.add(key)
                combined.append(hit)
                if len(combined) >= request.max_results:
                    break
            return tuple(combined[: request.max_results])

        results, elapsed_ms = timed(search)
        if not results and strategies and all(
            outcome.state is CapabilityState.UNAVAILABLE for outcome in strategies
        ):
            raise _both_unavailable_error(strategies)

        return ToolResult(
            results,
            elapsed_ms,
            truncated=len(results) >= request.max_results,
            warnings=normalize_warnings(warnings),
            index_state=status.state.value,
            strategies=tuple(strategies),
        )

    def _resolve_target(self, query: str):
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
            symbol
            for symbol in symbols
            if (symbol.qualified_name or "").casefold() == folded
        ]
        if qualified:
            return qualified[0], qualified[0].name
        named = [symbol for symbol in symbols if symbol.name.casefold() == folded]
        if len(named) == 1:
            return named[0], named[0].name
        if len(symbols) == 1:
            return symbols[0], symbols[0].name
        return None, simple_name

    def _owner_symbol(self, target_symbol):
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
            outline = self._store.get_outline(
                self._project.project_id, target_symbol.location.path
            )
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
            ):
                if enclosing is None or (
                    symbol.location.start_line >= enclosing.location.start_line
                    and symbol.location.end_line <= enclosing.location.end_line
                ):
                    enclosing = symbol
        return enclosing or target_symbol

    def _symbols_named(self, simple_name: str):
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
            if item.symbol is not None
            and item.symbol.name.casefold() == simple_name.casefold()
        )

    def _annotate_references(
        self,
        results: tuple[StructuralSearchResult, ...],
        target_symbol,
        simple_name: str,
        owner_symbol=None,
        *,
        private_target: bool = False,
        homonym_owners: tuple = (),
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
                elif private_target:
                    continue
                elif any(
                    _location_in_symbol(reference.location, other)
                    for other in homonym_owners
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
        target_symbol,
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
                    target_id = (
                        matching_defs[0].symbol_id if len(matching_defs) == 1 else None
                    )
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


def _looks_private(symbol) -> bool:
    header = (symbol.signature or symbol.canonical_signature or "").split("(", 1)[0]
    return "private" in header.casefold().split()


def _location_in_symbol(location, symbol) -> bool:
    return (
        location.path == symbol.location.path
        and symbol.location.start_line <= location.start_line <= symbol.location.end_line
    )


def _is_symbol_definition_line(location, symbol) -> bool:
    return (
        location.path == symbol.location.path
        and location.start_line == symbol.location.start_line
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
