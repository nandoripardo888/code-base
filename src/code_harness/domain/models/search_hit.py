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


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    hits: tuple[SearchHit, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    index_state: str | None = None
