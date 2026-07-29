from __future__ import annotations

from code_harness.application.dto.review_requests import ApplyReviewFixRequest
from code_harness.application.review.review_action_authorizer import ReviewActionAuthorizer
from code_harness.application.review.review_action_digest import (
    compute_review_action_digest,
    patch_sha256,
)
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ExecutionCapability
from code_harness.domain.errors import ReviewActionNotAllowedError, ReviewActionsDisabledError
from code_harness.domain.models.review_actions import ReviewFixApplication
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider
from code_harness.domain.protocols.patch_applier import PatchApplier


class ApplyReviewFixTool:
    def __init__(
        self,
        *,
        provider: ChangeProvider,
        applier: PatchApplier,
        authorizer: ReviewActionAuthorizer | None,
        enabled: bool = False,
        allowed: bool = False,
        project_id: str,
    ) -> None:
        self._provider = provider
        self._applier = applier
        self._authorizer = authorizer
        self._enabled = enabled
        self._allowed = allowed
        self._project_id = project_id

    def execute(self, request: ApplyReviewFixRequest) -> ToolResult[ReviewFixApplication]:
        def run() -> ReviewFixApplication:
            if not self._enabled:
                raise ReviewActionsDisabledError()
            if not self._allowed:
                raise ReviewActionNotAllowedError("apply_review_fix")
            change_set = self._provider.get_change_set(request.change_set_id)
            digest = compute_review_action_digest(
                project_id=self._project_id,
                action_kind="apply_patch",
                payload={
                    "change_set_id": change_set.change_set_id,
                    "expected_file_hashes": dict(sorted(request.expected_file_hashes)),
                    "patch_sha256": patch_sha256(request.patch_text),
                    "target_paths": sorted(path for path, _hash in request.expected_file_hashes),
                },
            )
            if self._authorizer is not None:
                self._authorizer.ensure_authorized(
                    digest=digest,
                    action_kind="apply_patch",
                    summary=request.reason or f"apply patch to {change_set.change_set_id}",
                    approval_id=request.approval_id,
                    approval_session_id=request.approval_session_id,
                    required_capabilities=(ExecutionCapability.WORKSPACE_WRITE.value,),
                )
            return self._applier.apply_unified_patch(
                change_set=change_set,
                patch_text=request.patch_text,
                expected_file_hashes=request.expected_file_hashes,
            )

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)
