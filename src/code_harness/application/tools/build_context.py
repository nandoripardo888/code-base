from dataclasses import dataclass, replace

from code_harness.application.context import estimate_tokens
from code_harness.application.dto.requests import BuildContextRequest, SearchCodeRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.search_code import SearchCodeTool
from code_harness.domain.enums import IndexState, MatchType
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.context import ContextBundle, ContextSnippet
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore

_ROLE_PRIORITY = {"definition": 0, "primary": 1, "parent": 2, "reference": 3}


@dataclass(frozen=True, slots=True)
class _PendingSnippet:
    snippet: CodeSnippet
    score: float
    role: str
    relation: str | None
    depth: int
    reason: str
    source_match_types: tuple[MatchType, ...]


def _header(item: _PendingSnippet) -> str:
    location = item.snippet.location
    relation = f" relation={item.relation}" if item.relation else ""
    return f"[{item.role}{relation}] {location.path}:{location.start_line}-{location.end_line}\n"


def _overlaps(left: _PendingSnippet, right: _PendingSnippet) -> bool:
    first = left.snippet.location
    second = right.snippet.location
    return (
        first.path == second.path
        and first.start_line <= second.end_line
        and second.start_line <= first.end_line
    )


class BuildContextTool:
    def __init__(
        self,
        search_code: SearchCodeTool,
        project: Project,
        store: RepositoryStore,
        reader: IndexSourceReader,
    ) -> None:
        self._search_code = search_code
        self._project = project
        self._store = store
        self._reader = reader

    def execute(self, request: BuildContextRequest) -> ToolResult[ContextBundle]:
        def build() -> ContextBundle:
            search = self._search_code.execute(
                SearchCodeRequest(
                    request.query,
                    request.include_globs,
                    request.exclude_globs,
                    request.languages,
                    max(request.max_snippets * 3, 50),
                    2,
                    10.0,
                )
            )
            warnings = list(search.warnings)
            pending = [self._seed(hit) for hit in search.data]
            expansions, expansion_warnings = self._expand(
                search.data,
                request.max_expansion_depth,
                request.max_snippets * 3,
            )
            pending.extend(expansions)
            warnings.extend(expansion_warnings)
            original_count = len(pending)
            pending = self._deduplicate(pending)
            available = request.max_tokens - request.reserved_tokens
            metadata_tokens = estimate_tokens(
                f"estimated_tokens={available}/{available}; omitted_results={original_count}"
            )
            snippets, selection_warnings = self._apply_budget(
                request,
                pending,
                metadata_tokens,
            )
            warnings.extend(selection_warnings)
            omitted = max(0, original_count - len(snippets)) + int(search.truncated)
            estimated = metadata_tokens + sum(item.estimated_tokens for item in snippets)
            unique_warnings = tuple(dict.fromkeys(warnings))
            return ContextBundle(
                request.query,
                snippets,
                omitted,
                estimated,
                available,
                unique_warnings,
            )

        bundle, elapsed_ms = timed(build)
        return ToolResult(
            bundle,
            elapsed_ms,
            truncated=bundle.omitted_results > 0,
            warnings=bundle.warnings,
        )

    @staticmethod
    def _seed(hit: HybridSearchHit) -> _PendingSnippet:
        match_types = tuple(item.match_type for item in hit.evidence)
        role = "definition" if MatchType.SYMBOL in match_types else "primary"
        return _PendingSnippet(
            hit.snippet,
            hit.score,
            role,
            None,
            0,
            hit.reason,
            match_types,
        )

    def _expand(
        self,
        hits: tuple[HybridSearchHit, ...],
        max_depth: int,
        limit: int,
    ) -> tuple[list[_PendingSnippet], list[str]]:
        if max_depth == 0:
            return [], []
        try:
            status = self._store.get_status(self._project)
        except CodeHarnessError as error:
            return [], [f"Context expansion is unavailable ({error.code.value})."]
        if (
            status.state not in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
            or not status.structural_schema_ready
        ):
            return [], ["Structural index is not ready; context was not expanded."]

        symbol_ids = tuple(
            dict.fromkeys(
                evidence.source_id
                for hit in hits
                for evidence in hit.evidence
                if evidence.match_type is MatchType.SYMBOL and evidence.source_id
            )
        )
        if not symbol_ids:
            return [], []
        current = self._store.find_symbols_by_ids(self._project.project_id, symbol_ids)
        seen_symbols = set(symbol_ids)
        seen_references: set[str] = set()
        expanded: list[_PendingSnippet] = []
        warnings: list[str] = []
        for depth in range(1, max_depth + 1):
            next_parent_ids: list[str] = []
            for result in current:
                symbol = result.symbol
                if symbol is None:
                    continue
                if symbol.parent_symbol_id and symbol.parent_symbol_id not in seen_symbols:
                    next_parent_ids.append(symbol.parent_symbol_id)
                    seen_symbols.add(symbol.parent_symbol_id)
                references = self._store.find_references(
                    self._project.project_id,
                    symbol.name,
                    limit=max(1, limit - len(expanded)),
                )
                for reference_result in references:
                    reference = reference_result.reference
                    if reference is None or reference.reference_id in seen_references:
                        continue
                    seen_references.add(reference.reference_id)
                    pending, warning = self._validated_structural(
                        reference_result,
                        role="reference",
                        relation=symbol.name,
                        depth=depth,
                        score=max(0.1, 0.70 - depth * 0.10),
                    )
                    if warning:
                        warnings.append(warning)
                    if pending is not None:
                        expanded.append(pending)
                    if len(expanded) >= limit:
                        return expanded, warnings
            parents = self._store.find_symbols_by_ids(
                self._project.project_id,
                tuple(next_parent_ids),
            )
            for parent_result in parents:
                parent = parent_result.symbol
                pending, warning = self._validated_structural(
                    parent_result,
                    role="parent",
                    relation=parent.name if parent else None,
                    depth=depth,
                    score=max(0.1, 0.80 - depth * 0.10),
                )
                if warning:
                    warnings.append(warning)
                if pending is not None:
                    expanded.append(pending)
                if len(expanded) >= limit:
                    return expanded, warnings
            current = parents
            if not current:
                break
        return expanded, warnings

    def _validated_structural(
        self,
        result: StructuralSearchResult,
        *,
        role: str,
        relation: str | None,
        depth: int,
        score: float,
    ) -> tuple[_PendingSnippet | None, str | None]:
        item = result.symbol or result.reference
        if item is None:
            return None, None
        try:
            source = self._reader.load(item.location.path)
        except CodeHarnessError as error:
            return None, (
                f"Skipped context expansion for {item.location.path}: "
                f"current file is unavailable ({error.code.value})."
            )
        if source.content_hash != result.file_hash:
            return None, f"Skipped stale context expansion for {item.location.path}; reindex it."
        lines = source.content.splitlines(keepends=True)
        location = item.location
        end_line = min(location.end_line, max(1, len(lines)))
        snippet = CodeSnippet(
            CodeLocation(location.path, location.start_line, end_line),
            "".join(lines[location.start_line - 1 : end_line]),
            source.language,
            source.content_hash,
        )
        match_type = MatchType.SYMBOL if result.symbol else MatchType.REFERENCE
        return (
            _PendingSnippet(
                snippet,
                score,
                role,
                relation,
                depth,
                f"Controlled {role} expansion from the structural index.",
                (match_type,),
            ),
            None,
        )

    @staticmethod
    def _deduplicate(values: list[_PendingSnippet]) -> list[_PendingSnippet]:
        ordered = sorted(
            values,
            key=lambda item: (
                _ROLE_PRIORITY.get(item.role, 9),
                -item.score,
                item.depth,
                item.snippet.location.path,
                item.snippet.location.start_line,
            ),
        )
        selected: list[_PendingSnippet] = []
        for value in ordered:
            if any(_overlaps(value, existing) for existing in selected):
                continue
            selected.append(value)
        return selected

    def _apply_budget(
        self,
        request: BuildContextRequest,
        pending: list[_PendingSnippet],
        metadata_tokens: int,
    ) -> tuple[tuple[ContextSnippet, ...], list[str]]:
        available = request.max_tokens - request.reserved_tokens
        remaining = max(0, available - metadata_tokens)
        files: set[str] = set()
        selected: list[ContextSnippet] = []
        warnings: list[str] = []
        sources: dict[str, IndexedSource | CodeHarnessError] = {}
        for item in pending:
            if len(selected) >= request.max_snippets:
                break
            path = item.snippet.location.path
            if path not in files and len(files) >= request.max_files:
                continue
            if path not in sources:
                try:
                    sources[path] = self._reader.load(path)
                except CodeHarnessError as error:
                    sources[path] = error
            source = sources[path]
            if isinstance(source, CodeHarnessError):
                warnings.append(
                    f"Skipped context snippet for {path}: "
                    f"current file is unavailable ({source.code.value})."
                )
                continue
            if source.content_hash != item.snippet.file_hash:
                warnings.append(f"Skipped stale context snippet for {path}; reindex it.")
                continue
            location = item.snippet.location
            lines = source.content.splitlines(keepends=True)
            end_line = min(location.end_line, max(1, len(lines)))
            item = replace(
                item,
                snippet=CodeSnippet(
                    CodeLocation(path, location.start_line, end_line),
                    "".join(lines[location.start_line - 1 : end_line]),
                    source.language,
                    source.content_hash,
                ),
            )
            estimate = estimate_tokens(_header(item) + item.snippet.content)
            selected_item = item
            truncated = False
            if estimate > remaining:
                clipped = self._clip(item, remaining)
                if clipped is None:
                    continue
                selected_item = clipped
                estimate = estimate_tokens(_header(selected_item) + selected_item.snippet.content)
                truncated = True
                warnings.append(f"Truncated context snippet for {path} to fit the token budget.")
            selected.append(
                ContextSnippet(
                    selected_item.snippet,
                    selected_item.score,
                    selected_item.role,
                    selected_item.relation,
                    selected_item.depth,
                    estimate,
                    selected_item.reason,
                    selected_item.source_match_types,
                    truncated,
                )
            )
            remaining -= estimate
            files.add(path)
            if remaining <= 0:
                break
        return tuple(selected), warnings

    @staticmethod
    def _clip(item: _PendingSnippet, available_tokens: int) -> _PendingSnippet | None:
        header_tokens = estimate_tokens(_header(item))
        if available_tokens <= header_tokens:
            return None
        allowed_bytes = (available_tokens - header_tokens) * 3
        chosen: list[str] = []
        used = 0
        for line in item.snippet.content.splitlines(keepends=True):
            encoded = len(line.encode("utf-8"))
            if used + encoded > allowed_bytes:
                break
            chosen.append(line)
            used += encoded
        if not chosen:
            return None
        location = item.snippet.location
        snippet = replace(
            item.snippet,
            location=CodeLocation(
                location.path,
                location.start_line,
                location.start_line + len(chosen) - 1,
            ),
            content="".join(chosen),
        )
        return replace(item, snippet=snippet)
