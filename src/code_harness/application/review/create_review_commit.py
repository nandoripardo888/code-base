from __future__ import annotations

from code_harness.application.dto.review_requests import CreateReviewCommitRequest
from code_harness.application.review.review_action_authorizer import ReviewActionAuthorizer
from code_harness.application.review.review_action_digest import compute_review_action_digest
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ExecutionCapability
from code_harness.domain.errors import ReviewActionNotAllowedError, ReviewActionsDisabledError
from code_harness.domain.models.review_actions import ReviewCommitResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider
from code_harness.domain.protocols.review_committer import ReviewCommitter
from code_harness.domain.protocols.workspace_snapshot import WorkspaceSnapshotProvider


class CreateReviewCommitTool:
    def __init__(
        self,
        *,
        provider: ChangeProvider,
        snapshots: WorkspaceSnapshotProvider,
        committer: ReviewCommitter | None,
        authorizer: ReviewActionAuthorizer | None,
        enabled: bool = False,
        allowed: bool = False,
        project_id: str,
    ) -> None:
        self._provider = provider
        self._snapshots = snapshots
        self._committer = committer
        self._authorizer = authorizer
        self._enabled = enabled
        self._allowed = allowed
        self._project_id = project_id

    def execute(self, request: CreateReviewCommitRequest) -> ToolResult[ReviewCommitResult]:
        def run() -> ReviewCommitResult:
            if not self._enabled:
                raise ReviewActionsDisabledError()
            if not self._allowed or self._committer is None:
                raise ReviewActionNotAllowedError("create_review_commit")
            change_set = self._provider.get_change_set(request.change_set_id)
            snapshot = self._snapshots.snapshot_for_change_set(change_set)
            digest = compute_review_action_digest(
                project_id=self._project_id,
                action_kind="create_commit",
                payload={
                    "change_set_id": change_set.change_set_id,
                    "message": request.message,
                    "paths": list(request.paths) if request.paths is not None else None,
                    "workspace_snapshot_digest": snapshot.digest,
                },
            )
            if self._authorizer is not None:
                self._authorizer.ensure_authorized(
                    digest=digest,
                    action_kind="create_commit",
                    summary=f"create commit: {request.message[:80]}",
                    approval_id=request.approval_id,
                    approval_session_id=request.approval_session_id,
                    required_capabilities=(ExecutionCapability.GIT_WRITE.value,),
                )
            return self._committer.create_commit(
                change_set=change_set,
                message=request.message,
                workspace_snapshot_digest=snapshot.digest,
                paths=request.paths,
            )

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)
