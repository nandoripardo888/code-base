from dataclasses import dataclass

from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation


@dataclass(frozen=True, slots=True)
class QueryClassification:
    kind: QueryKind
    signals: tuple[str, ...]
    identifiers: tuple[str, ...]
    lexical_terms: tuple[str, ...]
    path_terms: tuple[str, ...]
    original_identifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryPlan:
    query_terms: tuple[str, ...]
    target_terms: tuple[str, ...]
    anchor_name: str | None = None
    anchor_path: str | None = None
    anchor_symbol_id: str | None = None
    anchor_location: CodeLocation | None = None
    anchor_kind: str | None = None
    enumeration: bool = False

    @property
    def has_anchor(self) -> bool:
        return self.anchor_path is not None


@dataclass(frozen=True, slots=True)
class SearchScore:
    evidence: float
    coverage: float
    scope_bonus: float
    rrf_tiebreak: float


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
    snippet_truncated: bool = False
    source_location: CodeLocation | None = None
    scope: str = "fallback"
    query_coverage: float = 0.0
    score_components: SearchScore | None = None
    comment_only: bool = False
