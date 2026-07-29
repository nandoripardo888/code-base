from dataclasses import dataclass, field

from code_harness.domain.models.context import ContextSnippet


@dataclass(frozen=True, slots=True)
class ImpactReference:
    path: str
    start_line: int
    end_line: int
    kind: str
    confidence: float = 1.0
    symbol_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImpactTest:
    path: str
    reason: str
    score: float


@dataclass(frozen=True, slots=True)
class ChangeImpact:
    symbol_id: str
    name: str
    qualified_name: str
    path: str
    kind: str
    callers: tuple[ImpactReference, ...] = ()
    references: tuple[ImpactReference, ...] = ()
    related_tests: tuple[ImpactTest, ...] = ()
    config_files: tuple[ImpactReference, ...] = ()
    implementations: tuple[ImpactReference, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewContextBundle:
    change_set_id: str
    snippets: tuple[ContextSnippet, ...]
    estimated_tokens: int
    available_tokens: int
    omitted: dict[str, int] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    coverage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    change_set_id: str
    candidate_tests: tuple[str, ...]
    suggested_commands: tuple[str, ...]
    affected_modules: tuple[str, ...]
    static_checks: tuple[str, ...]
    limitations: tuple[str, ...] = ()
