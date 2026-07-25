from collections.abc import Mapping
from dataclasses import dataclass

from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.result_truncation import ResultTruncation


@dataclass(frozen=True, slots=True)
class SearchHit:
    snippet: CodeSnippet
    score: float
    match_type: MatchType
    matched_terms: tuple[str, ...]
    reason: str | None = None
    match_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None
    validated: bool = False
    evidence: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    hits: tuple[SearchHit, ...]
    truncated: bool = False
    truncation: ResultTruncation | None = None
    warnings: tuple[str, ...] = ()
    index_state: str | None = None

    def __post_init__(self) -> None:
        if self.truncation is not None and self.truncation.truncated and not self.truncated:
            object.__setattr__(self, "truncated", True)
