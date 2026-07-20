from dataclasses import dataclass

from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.models.code_chunk import CodeSnippet


@dataclass(frozen=True, slots=True)
class QueryClassification:
    kind: QueryKind
    signals: tuple[str, ...]
    identifiers: tuple[str, ...]
    lexical_terms: tuple[str, ...]
    path_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    match_type: MatchType
    rank: int
    raw_score: float
    normalized_score: float
    contribution: float
    source_id: str | None = None
    source_name: str | None = None


@dataclass(frozen=True, slots=True)
class HybridSearchHit:
    snippet: CodeSnippet
    score: float
    evidence: tuple[SearchEvidence, ...]
    matched_terms: tuple[str, ...]
    reason: str
