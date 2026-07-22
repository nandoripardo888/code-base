from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from code_harness.domain.enums import CapabilityState


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    state: CapabilityState
    optional: bool
    enabled: bool
    root_cause: str | None = None
    remediation: str | None = None
    last_health_check: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolWarning:
    code: str
    message: str
    recoverable: bool
    capability: str | None = None
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    strategy: str
    state: CapabilityState
    hit_count: int = 0
    elapsed_ms: int = 0
    warning: ToolWarning | None = None
    error_code: str | None = None
