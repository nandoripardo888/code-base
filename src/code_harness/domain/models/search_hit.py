from collections.abc import Mapping
from dataclasses import dataclass

from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet


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
    warnings: tuple[str, ...] = ()
    index_state: str | None = None
