from typing import Protocol

from code_harness.domain.models.search_hit import SearchOutcome


class TextSearcher(Protocol):
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
    ) -> SearchOutcome: ...
