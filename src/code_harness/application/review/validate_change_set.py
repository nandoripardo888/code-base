from __future__ import annotations

from pathlib import Path

from code_harness.application.dto.execution_requests import RunProcessRequest
from code_harness.application.dto.review_requests import ValidateChangeSetRequest
from code_harness.application.execution.run_process import RunProcessTool
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ExecutionCapability
from code_harness.domain.errors import (
    ExecutionDisabledError,
    ReviewActionsDisabledError,
)
from code_harness.domain.models.review_actions import ValidationRunResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider
from code_harness.domain.protocols.isolated_worktree import IsolatedWorktreeFactory
from code_harness.domain.protocols.workspace_snapshot import WorkspaceSnapshotProvider


class ValidateChangeSetTool:
    def __init__(
        self,
        *,
        provider: ChangeProvider,
        snapshots: WorkspaceSnapshotProvider,
        run_process: RunProcessTool | None,
        project_root: str | Path,
        worktree_factory: IsolatedWorktreeFactory | None = None,
        enabled: bool = False,
        default_use_worktree: bool = False,
    ) -> None:
        self._provider = provider
        self._snapshots = snapshots
        self._run_process = run_process
        self._project_root = Path(project_root)
        self._worktree_factory = worktree_factory
        self._enabled = enabled
        self._default_use_worktree = default_use_worktree

    def execute(self, request: ValidateChangeSetRequest) -> ToolResult[ValidationRunResult]:
        def run() -> ValidationRunResult:
            if not self._enabled:
                raise ReviewActionsDisabledError()
            if self._run_process is None:
                raise ExecutionDisabledError()
            change_set = self._provider.get_change_set(request.change_set_id)
            snapshot = self._snapshots.snapshot_for_change_set(change_set)
            capabilities = tuple(
                ExecutionCapability(item) if not isinstance(item, ExecutionCapability) else item
                for item in request.requested_capabilities
            )
            use_worktree = (
                self._default_use_worktree
                if request.use_worktree is None
                else request.use_worktree
            )
            digest_context = (
                ("change_set_id", change_set.change_set_id),
                ("workspace_snapshot_digest", snapshot.digest),
            )
            if use_worktree:
                if self._worktree_factory is None:
                    raise ReviewActionsDisabledError(
                        "Isolated worktree support is not configured."
                    )
                diff = self._provider.read_diff(change_set)
                with self._worktree_factory.create(
                    change_set_id=change_set.change_set_id,
                    base_ref=change_set.base_ref,
                    unified_diff=diff.unified_text,
                ) as worktree:
                    cwd = _relative_cwd(request.cwd, worktree, self._project_root)
                    execution = self._run_process.execute(
                        RunProcessRequest(
                            executable=request.executable,
                            args=request.args,
                            cwd=cwd,
                            timeout_seconds=request.timeout_seconds,
                            max_output_bytes=request.max_output_bytes,
                            requested_capabilities=capabilities,
                            reason=request.reason,
                            approval_id=request.approval_id,
                            approval_session_id=request.approval_session_id,
                            wait=request.wait,
                            digest_context=digest_context,
                        )
                    ).data
            else:
                execution = self._run_process.execute(
                    RunProcessRequest(
                        executable=request.executable,
                        args=request.args,
                        cwd=request.cwd,
                        timeout_seconds=request.timeout_seconds,
                        max_output_bytes=request.max_output_bytes,
                        requested_capabilities=capabilities,
                        reason=request.reason,
                        approval_id=request.approval_id,
                        approval_session_id=request.approval_session_id,
                        wait=request.wait,
                        digest_context=digest_context,
                    )
                ).data
            return ValidationRunResult(
                change_set_id=change_set.change_set_id,
                workspace_snapshot_digest=snapshot.digest,
                execution=execution,
                used_worktree=use_worktree,
            )

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)


def _relative_cwd(requested_cwd: str, worktree: Path, project_root: Path) -> str:
    candidate = Path(requested_cwd)
    target = worktree if candidate.is_absolute() else (worktree / candidate)
    try:
        return str(target.resolve(strict=False).relative_to(project_root.resolve(strict=False)))
    except ValueError:
        return str(worktree.relative_to(project_root.resolve(strict=False)))
