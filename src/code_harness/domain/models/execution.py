from dataclasses import dataclass

from code_harness.domain.enums import (
    CommandKind,
    ExecutionCapability,
    ExecutionRiskSeverity,
    PolicyDecision,
)


@dataclass(frozen=True, slots=True)
class ProcessArgument:
    value: str


@dataclass(frozen=True, slots=True)
class PolicyReason:
    code: str
    message: str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class RiskFinding:
    code: str
    severity: ExecutionRiskSeverity
    message: str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDigest:
    value: str
    algorithm: str = "sha256"


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeConfig:
    """Inspection-time configuration derived from Settings (no bootstrap import)."""

    backend: str
    require_approval: bool
    default_timeout_seconds: float
    max_timeout_seconds: float
    max_output_bytes: int
    allow_elevated: bool
    execution_home: str
    project_id: str
    policy_name: str = "deterministic_v1"
    policy_version: str = "1"
    elevated_session: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedProcessCommand:
    executable: str
    args: tuple[str, ...]
    cwd: str
    timeout_seconds: float
    max_output_bytes: int
    requested_capabilities: tuple[ExecutionCapability, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class NormalizedPowerShellCommand:
    script: str
    cwd: str
    timeout_seconds: float
    max_output_bytes: int
    requested_capabilities: tuple[ExecutionCapability, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class CommandInspection:
    kind: CommandKind
    decision: PolicyDecision
    requested_capabilities: tuple[ExecutionCapability, ...]
    required_capabilities: tuple[ExecutionCapability, ...]
    approval_required: bool
    reasons: tuple[PolicyReason, ...]
    risks: tuple[RiskFinding, ...]
    blocks: tuple[PolicyReason, ...]
    approval_digest: ApprovalDigest | None
    cwd: str
    timeout_seconds: float
    max_output_bytes: int
    executable: str | None = None
    args: tuple[str, ...] = ()
    script_hash: str | None = None
    dynamic_features: tuple[str, ...] = ()
    protected_path_matches: tuple[str, ...] = ()
    backend: str = "host"
    policy_name: str = "deterministic_v1"
    policy_version: str = "1"
    warnings: tuple[str, ...] = ()
