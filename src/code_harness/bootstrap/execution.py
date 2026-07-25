"""Optional execution subsystem composition (inspection-only in E0)."""

from __future__ import annotations

from dataclasses import dataclass

from code_harness.application.execution import (
    DeterministicPolicyEngine,
    InspectPowerShellTool,
    InspectProcessTool,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.models.execution import BackendGuarantees, ExecutionRuntimeConfig
from code_harness.domain.models.project import Project
from code_harness.domain.protocols.command_policy import WorkspacePathResolver


@dataclass(frozen=True, slots=True)
class ExecutionContainer:
    inspect_process: InspectProcessTool
    inspect_powershell: InspectPowerShellTool


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
        allow_elevated=settings.execution_allow_elevated,
        execution_home=str(settings.execution_project_home()),
        project_id=project.project_id,
        backend_guarantees=_backend_guarantees(settings.execution_backend),
        powershell_executable=settings.execution_powershell_executable,
        elevated_session=elevated_session,
    )
    policy = DeterministicPolicyEngine(config)
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer

    return ExecutionContainer(
        inspect_process=InspectProcessTool(paths=paths, policy=policy, config=config),
        inspect_powershell=InspectPowerShellTool(
            paths=paths,
            policy=policy,
            analyzer=PowerShellAstAnalyzer(settings.execution_powershell_executable),
            config=config,
        ),
    )


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
