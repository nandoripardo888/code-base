from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class ContextBundle:
    query: str
    snippets: tuple[ContextSnippet, ...]
    omitted_results: int
    estimated_tokens: int
    available_tokens: int
    warnings: tuple[str, ...] = ()
