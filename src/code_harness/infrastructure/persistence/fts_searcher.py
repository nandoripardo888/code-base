from code_harness.domain.enums import IndexState, MatchType
from code_harness.domain.errors import CodeHarnessError, RipgrepUnavailableError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.project import Project
from code_harness.domain.models.search_hit import SearchHit, SearchOutcome
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.text_searcher import TextSearcher
from code_harness.infrastructure.filesystem.ignore_rules import compile_globs


class IndexedTextSearcher:
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        reader: IndexSourceReader,
        fallback: TextSearcher,
    ) -> None:
        self._project = project
        self._store = store
        self._reader = reader
        self._fallback = fallback

    def search(
        self,
        *,
        query: str,
        regex: bool,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        case_sensitive: bool,
        max_results: int,
        context_lines: int,
        timeout_seconds: float,
    ) -> SearchOutcome:
        if regex:
            fallback = self._fallback.search(
                query=query,
                regex=True,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                case_sensitive=case_sensitive,
                max_results=max_results,
                context_lines=context_lines,
                timeout_seconds=timeout_seconds,
            )
            index_state = IndexState.NOT_INITIALIZED.value
            try:
                status = self._store.get_status(self._project)
                index_state = status.state.value
            except CodeHarnessError:
                index_state = IndexState.FAILED.value
            return SearchOutcome(
                fallback.hits,
                truncated=fallback.truncated,
                warnings=fallback.warnings,
                index_state=index_state,
            )

        indexed_hits: tuple[SearchHit, ...] = ()
        warnings: list[str] = []
        index_state = IndexState.NOT_INITIALIZED.value
        try:
            status = self._store.get_status(self._project)
            index_state = status.state.value
            if status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS):
                indexed_hits = self._indexed_hits(
                    query,
                    include_globs=include_globs,
                    exclude_globs=exclude_globs,
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                    context_lines=context_lines,
                )
        except CodeHarnessError as error:
            index_state = IndexState.FAILED.value
            warnings.append(
                f"Indexed search is unavailable ({error.code.value}); used Ripgrep fallback."
            )

        try:
            fallback = self._fallback.search(
                query=query,
                regex=False,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                case_sensitive=case_sensitive,
                max_results=max_results,
                context_lines=context_lines,
                timeout_seconds=timeout_seconds,
            )
        except RipgrepUnavailableError:
            if not indexed_hits:
                raise
            warnings.append("Ripgrep is unavailable; returned validated indexed results only.")
            return SearchOutcome(indexed_hits, warnings=tuple(warnings), index_state=index_state)

        combined = _merge_hits(indexed_hits, fallback.hits, max_results)
        warnings.extend(fallback.warnings)
        return SearchOutcome(
            combined,
            truncated=fallback.truncated or len(indexed_hits) + len(fallback.hits) > len(combined),
            warnings=tuple(dict.fromkeys(warnings)),
            index_state=index_state,
        )

    def _indexed_hits(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        case_sensitive: bool,
        max_results: int,
        context_lines: int,
    ) -> tuple[SearchHit, ...]:
        include_spec = compile_globs(include_globs)
        exclude_spec = compile_globs(exclude_globs)
        candidates = self._store.search_fts(
            self._project.project_id, query, limit=max(max_results * 5, 50)
        )
        hits: list[SearchHit] = []
        for candidate in candidates:
            if include_spec is not None and not include_spec.match_file(candidate.path):
                continue
            if exclude_spec is not None and exclude_spec.match_file(candidate.path):
                continue
            try:
                source = self._reader.load(candidate.path)
            except CodeHarnessError:
                continue
            lines = source.content.splitlines(keepends=True)
            needle = query if case_sensitive else query.casefold()
            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                start = max(1, line_number - context_lines)
                end = min(len(lines), line_number + context_lines)
                content = "".join(lines[start - 1 : end])
                column = haystack.find(needle)
                start_column = column + 1 if column >= 0 else None
                end_column = (
                    start_column + len(query) - 1 if start_column is not None else None
                )
                match_type = (
                    MatchType.EXACT_LITERAL
                    if haystack.strip() == needle
                    else MatchType.SUBSTRING
                )
                hits.append(
                    SearchHit(
                        snippet=CodeSnippet(
                            CodeLocation(candidate.path, start, max(start, end)),
                            content,
                            source.language,
                            source.content_hash,
                        ),
                        score=1.0 / (1.0 + abs(candidate.rank)),
                        match_type=match_type,
                        matched_terms=(query,),
                        reason="SQLite FTS candidate validated against the current file.",
                        match_line=line_number,
                        start_column=start_column,
                        end_column=end_column,
                        validated=True,
                        evidence={"candidate_source": "fts", "validated": True},
                    )
                )
                if len(hits) >= max_results:
                    return tuple(hits)
        return tuple(hits)


def _merge_hits(
    indexed: tuple[SearchHit, ...], fallback: tuple[SearchHit, ...], limit: int
) -> tuple[SearchHit, ...]:
    merged: list[SearchHit] = []
    seen: set[tuple[str, int, int]] = set()
    for hit in (*indexed, *fallback):
        location = hit.snippet.location
        key = (location.path, location.start_line, location.end_line)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return tuple(merged)
