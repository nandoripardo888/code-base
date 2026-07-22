from dataclasses import dataclass, field

from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet


@dataclass(frozen=True, slots=True)
class ContextSnippet:
    snippet: CodeSnippet
    score: float
    role: str
    relation: str | None
    depth: int
    estimated_tokens: int
    reason: str
    source_match_types: tuple[MatchType, ...] = ()
    truncated: bool = False
    ranges: tuple[tuple[int, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ContextBundle:
    query: str
    snippets: tuple[ContextSnippet, ...]
    omitted_results: int
    estimated_tokens: int
    available_tokens: int
    warnings: tuple[str, ...] = ()
    considered_results: int = 0
    selected_results: int = 0
    omitted: dict[str, int] = field(default_factory=dict)
    results_truncated: bool = False
    snippet_truncated: bool = False
    budget_exhausted: bool = False
    expansion_limited: bool = False
