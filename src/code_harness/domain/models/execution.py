from dataclasses import dataclass, field

from code_harness.domain.enums import (
    ApprovalState,
    CommandKind,
    ExecutionCapability,
    ExecutionRiskSeverity,
    ExecutionState,
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
class BackendGuarantees:
    """Security guarantees that the selected backend actively applies."""

    backend: str
    execution_available: bool
    process_tree_containment: bool
    timeout_enforced: bool
    output_limit_enforced: bool
    filesystem_isolated: bool
    network_isolated: bool
    credentials_isolated: bool


@dataclass(frozen=True, slots=True)
class PowerShellAstAnalysis:
    """Non-executing facts extracted from a PowerShell AST."""

    commands: tuple[str, ...]
    text_fragments: tuple[str, ...]
    dynamic_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeConfig:
    """Inspection-time configuration derived from Settings (no bootstrap import)."""

    backend: str
    require_approval: bool
    default_timeout_seconds: float
    max_timeout_seconds: float
    max_output_bytes: int
    max_processes: int
    allow_elevated: bool
    execution_home: str
    project_id: str
    max_concurrent: int = 1
    backend_guarantees: BackendGuarantees = field(
        default_factory=lambda: BackendGuarantees(
            backend="host_supervised",
            execution_available=False,
            process_tree_containment=False,
            timeout_enforced=False,
            output_limit_enforced=False,
            filesystem_isolated=False,
            network_isolated=False,
            credentials_isolated=False,
        )
    )
    powershell_enabled: bool = False
    powershell_executable: str = "pwsh"
    policy_name: str = "deterministic_v1"
    policy_version: str = "1"
    approval_ttl_seconds: int = 600
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
    resolved_executable: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedPowerShellCommand:
    script: str
    cwd: str
    timeout_seconds: float
    max_output_bytes: int
    requested_capabilities: tuple[ExecutionCapability, ...]
    reason: str | None
    resolved_executable: str | None = None


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
    backend_guarantees: BackendGuarantees = field(
        default_factory=lambda: BackendGuarantees(
            backend="host_supervised",
            execution_available=False,
            process_tree_containment=False,
            timeout_enforced=False,
            output_limit_enforced=False,
            filesystem_isolated=False,
            network_isolated=False,
            credentials_isolated=False,
        )
    )
    executable: str | None = None
    resolved_executable: str | None = None
    args: tuple[str, ...] = ()
    script_hash: str | None = None
    dynamic_features: tuple[str, ...] = ()
    protected_path_matches: tuple[str, ...] = ()
    backend: str = "host_supervised"
    policy_name: str = "deterministic_v1"
    policy_version: str = "1"
    ruleset_hash: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessRunOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    elapsed_ms: int
    cancelled: bool = False
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of a synchronous supervised process run."""

    execution_id: str
    state: ExecutionState
    inspection: CommandInspection
    backend_guarantees: BackendGuarantees
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_ms: int
    started_at: str | None = None
    finished_at: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionApproval:
    approval_id: str
    project_id: str
    digest: str
    state: ApprovalState
    command_kind: CommandKind
    command_summary: str
    required_capabilities: tuple[ExecutionCapability, ...]
    backend: str
    policy_name: str
    policy_version: str
    ruleset_hash: str
    created_at: str
    expires_at: str
    decided_at: str | None = None
    consumed_at: str | None = None
    decision_reason: str | None = None
    decision_source: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionAuditStart:
    execution_id: str
    project_id: str
    digest: str | None
    state: ExecutionState
    command_kind: CommandKind
    command_summary: str
    requested_capabilities: tuple[ExecutionCapability, ...]
    required_capabilities: tuple[ExecutionCapability, ...]
    backend: str
    backend_guarantees_json: str
    policy_decision: PolicyDecision
    policy_name: str
    policy_version: str
    ruleset_hash: str
    created_at: str
    slot_index: int | None = None
    approval_session_id: str | None = None
