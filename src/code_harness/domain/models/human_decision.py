from dataclasses import dataclass

from code_harness.domain.enums import HumanDecisionOutcome
from code_harness.domain.models.execution import RiskFinding


@dataclass(frozen=True, slots=True)
class DecisionDetail:
    key: str
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class DecisionOption:
    option_id: str
    label: str
    maps_to: HumanDecisionOutcome


@dataclass(frozen=True, slots=True)
class HumanDecisionRequest:
    request_id: str
    subject_kind: str
    subject_digest: str
    title: str
    summary: str
    details: tuple[DecisionDetail, ...]
    risks: tuple[RiskFinding, ...]
    options: tuple[DecisionOption, ...]
    expires_at: str


@dataclass(frozen=True, slots=True)
class HumanDecisionResult:
    outcome: HumanDecisionOutcome
    source: str
    reason: str | None = None
