"""Optional execution subsystem composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from code_harness.application.approvals import (
    ExecutionApprovalPresenter,
    InteractiveApprovalService,
)
from code_harness.application.execution import (
    ApprovalAdminTool,
    DeterministicPolicyEngine,
    GetExecutionTool,
    InspectPowerShellTool,
    InspectProcessTool,
    RunPowerShellTool,
    RunProcessTool,
    TerminateExecutionTool,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ApprovalChannel
from code_harness.domain.models.execution import BackendGuarantees, ExecutionRuntimeConfig
from code_harness.domain.models.project import Project
from code_harness.domain.protocols.command_policy import (
    SensitiveValueRedactor,
    WorkspacePathResolver,
)
from code_harness.domain.protocols.execution_store import ApprovalStore
from code_harness.domain.protocols.human_decision_channel import HumanDecisionChannel

if TYPE_CHECKING:
    from code_harness.domain.protocols.execution_runtime import ExecutionRegistry
    from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
    from code_harness.infrastructure.execution.runners import ProjectExecutionLimiter


@dataclass(frozen=True, slots=True)
class ExecutionContainer:
    inspect_process: InspectProcessTool
    inspect_powershell: InspectPowerShellTool
    run_process: RunProcessTool
    run_powershell: RunPowerShellTool
    get_execution: GetExecutionTool
    terminate_execution: TerminateExecutionTool
    approvals: ApprovalAdminTool
    interactive_approvals: InteractiveApprovalService
    redactor: SensitiveValueRedactor
    approval_channel: HumanDecisionChannel | None
    approval_store: ApprovalStore
    _registry: ExecutionRegistry

    def shutdown(self) -> None:
        if self.approval_channel is not None:
            self.approval_channel.shutdown()
        self._registry.shutdown()


def build_execution_container(
    settings: Settings,
    project: Project,
    paths: WorkspacePathResolver,
    *,
    elevated_session: bool = False,
) -> ExecutionContainer:
    """Build inspection tools. Must only be called when execution_enabled is true."""

    config = ExecutionRuntimeConfig(
        backend=settings.execution_backend,
        require_approval=settings.execution_require_approval,
        default_timeout_seconds=settings.execution_default_timeout_seconds,
        max_timeout_seconds=settings.execution_max_timeout_seconds,
        max_output_bytes=settings.execution_max_output_bytes,
        max_processes=settings.execution_max_processes,
        max_concurrent=settings.execution_max_concurrent,
        allow_elevated=settings.execution_allow_elevated,
        execution_home=str(settings.execution_project_home()),
        project_id=project.project_id,
        backend_guarantees=_backend_guarantees(settings.execution_backend),
        powershell_executable=settings.execution_powershell_executable,
        approval_ttl_seconds=settings.execution_approval_ttl_seconds,
        elevated_session=elevated_session,
        powershell_enabled=settings.execution_powershell_enabled,
    )
    policy = DeterministicPolicyEngine(config)
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer
    from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
    from code_harness.infrastructure.execution.redaction import SensitiveDataRedactor
    from code_harness.infrastructure.execution.runners import (
        HostExecutableResolver,
        PowerShell7ExecutableResolver,
        ProcessRegistry,
        ProjectExecutionLimiter,
        SupervisedPowerShellRunner,
        SupervisedProcessRunner,
    )

    store = SQLiteExecutionStore(settings.execution_store_path())
    store.initialize()
    limiter = ProjectExecutionLimiter(
        settings.execution_project_home(),
        max_concurrent=settings.execution_max_concurrent,
    )
    _recover_interrupted(store, limiter, project.project_id)
    registry = ProcessRegistry(limiter)
    redactor = SensitiveDataRedactor(project.root)
    inspect_process = InspectProcessTool(
        paths=paths,
        policy=policy,
        config=config,
        executable_resolver=HostExecutableResolver(),
    )
    inspect_powershell = InspectPowerShellTool(
        paths=paths,
        policy=policy,
        analyzer=PowerShellAstAnalyzer(settings.execution_powershell_executable),
        executable_resolver=PowerShell7ExecutableResolver(),
        config=config,
    )
    process_runner = SupervisedProcessRunner(
        execution_home=config.execution_home,
        max_processes=config.max_processes,
        allow_elevated=config.allow_elevated,
    )

    approvals = ApprovalAdminTool(
        project_id=project.project_id,
        store=store,
        redact=redactor,
    )
    approval_channel = _build_approval_channel(settings)
    return ExecutionContainer(
        inspect_process=inspect_process,
        inspect_powershell=inspect_powershell,
        run_process=RunProcessTool(
            inspect_process=inspect_process,
            runner=process_runner,
            project_id=project.project_id,
            store=store,
            approvals=store,
            redactor=redactor,
            approval_ttl_seconds=config.approval_ttl_seconds,
            require_approval=config.require_approval,
            registry=registry,
        ),
        run_powershell=RunPowerShellTool(
            inspect_powershell=inspect_powershell,
            runner=SupervisedPowerShellRunner(
                execution_home=config.execution_home,
                process_runner=process_runner,
            ),
            powershell_enabled=config.powershell_enabled,
            project_id=project.project_id,
            store=store,
            approvals=store,
            redactor=redactor,
            approval_ttl_seconds=config.approval_ttl_seconds,
            registry=registry,
        ),
        get_execution=GetExecutionTool(registry),
        terminate_execution=TerminateExecutionTool(
            registry=registry,
            store=store,
            redactor=redactor,
        ),
        approvals=approvals,
        interactive_approvals=InteractiveApprovalService(
            project_id=project.project_id,
            approvals=approvals,
            presenter=ExecutionApprovalPresenter(redact=redactor.redact),
        ),
        redactor=redactor,
        approval_channel=approval_channel,
        approval_store=store,
        _registry=registry,
    )


def _build_approval_channel(settings: Settings) -> HumanDecisionChannel | None:
    if settings.mcp_execution_approval_channel is not ApprovalChannel.HOST_LOOPBACK:
        return None
    from code_harness.infrastructure.interaction import HostLoopbackDecisionChannel

    return HostLoopbackDecisionChannel(
        port=settings.host_loopback_port,
        open_browser=settings.host_loopback_open_browser,
    )


def _recover_interrupted(
    store: SQLiteExecutionStore,
    limiter: ProjectExecutionLimiter,
    project_id: str,
) -> None:
    recovered_at = datetime.now(UTC).isoformat()
    for execution_id, slot_index in store.list_active_slots(project_id):
        if slot_index is None:
            store.recover_interrupted(execution_id, finished_at=recovered_at)
            continue
        lease = limiter.try_acquire_slot(slot_index)
        if lease is None:
            continue
        try:
            store.recover_interrupted(execution_id, finished_at=recovered_at)
        finally:
            lease.release()


def _backend_guarantees(backend: str) -> BackendGuarantees:
    if backend == "host_supervised":
        from code_harness.infrastructure.execution.backends import HostSupervisedBackend

        return HostSupervisedBackend().guarantees()
    return BackendGuarantees(
        backend=backend,
        execution_available=False,
        process_tree_containment=False,
        timeout_enforced=False,
        output_limit_enforced=False,
        filesystem_isolated=False,
        network_isolated=False,
        credentials_isolated=False,
    )
