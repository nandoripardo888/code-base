from __future__ import annotations

from code_harness.application.dto.review_requests import PublishReviewRequest
from code_harness.application.review.review_action_authorizer import ReviewActionAuthorizer
from code_harness.application.review.review_action_digest import compute_review_action_digest
from code_harness.application.tools._timing import timed
from code_harness.domain.errors import ReviewActionNotAllowedError, ReviewActionsDisabledError
from code_harness.domain.models.review_actions import PublishedReview, ReviewComment
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider
from code_harness.domain.protocols.review_publisher import ReviewPublisher


class PublishReviewTool:
    def __init__(
        self,
        *,
        provider: ChangeProvider,
        publisher: ReviewPublisher | None,
        authorizer: ReviewActionAuthorizer | None,
        enabled: bool = False,
        allowed: bool = False,
        project_id: str,
    ) -> None:
        self._provider = provider
        self._publisher = publisher
        self._authorizer = authorizer
        self._enabled = enabled
        self._allowed = allowed
        self._project_id = project_id

    def execute(self, request: PublishReviewRequest) -> ToolResult[PublishedReview]:
        def run() -> PublishedReview:
            if not self._enabled:
                raise ReviewActionsDisabledError()
            if not self._allowed or self._publisher is None:
                raise ReviewActionNotAllowedError("publish_review")
            change_set = self._provider.get_change_set(request.change_set_id)
            comments = tuple(
                ReviewComment(
                    path=path,
                    body=body,
                    start_line=start_line,
                    end_line=end_line,
                    side=side or "RIGHT",
                )
                for path, body, start_line, end_line, side in request.comments
            )
            digest = compute_review_action_digest(
                project_id=self._project_id,
                action_kind="publish_review",
                payload={
                    "body": request.body,
                    "change_set_id": change_set.change_set_id,
                    "comments": [
                        {
                            "body": item.body,
                            "end_line": item.end_line,
                            "path": item.path,
                            "side": item.side,
                            "start_line": item.start_line,
                        }
                        for item in comments
                    ],
                    "head_sha": change_set.head_sha,
                    "repository_id": change_set.repository_id,
                    "title": request.title,
                },
            )
            if self._authorizer is not None:
                self._authorizer.ensure_authorized(
                    digest=digest,
                    action_kind="publish_review",
                    summary=f"publish review: {request.title}",
                    approval_id=request.approval_id,
                    approval_session_id=request.approval_session_id,
                )
            return self._publisher.publish(
                change_set=change_set,
                title=request.title,
                body=request.body,
                comments=comments,
            )

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)
