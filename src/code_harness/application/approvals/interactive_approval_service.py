from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

from code_harness.application.approvals.execution_approval_presenter import (
    ExecutionApprovalPresenter,
)
from code_harness.application.execution.approval_admin import ApprovalAdminTool
from code_harness.domain.enums import ApprovalDecisionSource, HumanDecisionOutcome
from code_harness.domain.errors import (
    ExecutionApprovalDeniedError,
    ExecutionApprovalRequiredError,
)
from code_harness.domain.models.execution import CommandInspection
from code_harness.domain.models.human_decision import HumanDecisionResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.human_decision_channel import HumanDecisionChannel

T = TypeVar("T")

_DEFAULT_REUSABLE_SOURCES = (ApprovalDecisionSource.LOCAL_ADMIN.value,)
_CONFIRMATION_LOCKS: dict[str, threading.Lock] = {}
_CONFIRMATION_LOCKS_GUARD = threading.Lock()


def _claim_confirmation(approval_id: str) -> threading.Lock | None:
    with _CONFIRMATION_LOCKS_GUARD:
        lock = _CONFIRMATION_LOCKS.setdefault(approval_id, threading.Lock())
        return lock if lock.acquire(blocking=False) else None


def _release_confirmation(approval_id: str, lock: threading.Lock) -> None:
    lock.release()
    with _CONFIRMATION_LOCKS_GUARD:
        if not lock.locked():
            _CONFIRMATION_LOCKS.pop(approval_id, None)


class InteractiveApprovalService:
    """Orchestrates reusable approvals and interactive human decisions."""

    def __init__(
        self,
        *,
        project_id: str,
        approvals: ApprovalAdminTool,
        presenter: ExecutionApprovalPresenter,
        reusable_sources: tuple[str, ...] = _DEFAULT_REUSABLE_SOURCES,
    ) -> None:
        self._project_id = project_id
        self._approvals = approvals
        self._presenter = presenter
        self._reusable_sources = reusable_sources

    async def run_with_optional_approval(
        self,
        *,
        channel: HumanDecisionChannel | None,
        inspection: CommandInspection,
        run: Callable[[str | None, str | None], ToolResult[T]],
        timeout_seconds: float,
        decision_session_id: str | None = None,
    ) -> ToolResult[T]:
        digest = inspection.approval_digest.value if inspection.approval_digest else None
        reusable = None
        if digest is not None:
            reusable = self._approvals.find_reusable(
                digest=digest,
                allowed_sources=self._reusable_sources,
            )
        try:
            return run(reusable.approval_id if reusable else None, None)
        except ExecutionApprovalRequiredError as required:
            if channel is None:
                raise
            approval_id = required.details.get("approval_id")
            if not isinstance(approval_id, str):
                raise
            confirmation_lock = _claim_confirmation(approval_id)
            if confirmation_lock is None:
                required.details["elicitation_action"] = "already_in_progress"
                raise
            try:
                expires_at = required.details.get("expires_at")
                request = self._presenter.present(
                    inspection,
                    project_id=self._project_id,
                    approval_id=approval_id,
                    expires_at=expires_at if isinstance(expires_at, str) else "",
                )
                decision = await channel.request_decision(
                    request,
                    timeout_seconds=timeout_seconds,
                )
                return self._apply_decision(
                    decision,
                    approval_id=approval_id,
                    required=required,
                    run=run,
                    decision_session_id=decision_session_id,
                )
            finally:
                _release_confirmation(approval_id, confirmation_lock)

    def _apply_decision(
        self,
        decision: HumanDecisionResult,
        *,
        approval_id: str,
        required: ExecutionApprovalRequiredError,
        run: Callable[[str | None, str | None], ToolResult[T]],
        decision_session_id: str | None,
    ) -> ToolResult[T]:
        outcome = decision.outcome
        source = decision.source
        session_id = decision_session_id
        if source not in {
            ApprovalDecisionSource.MCP_ELICITATION.value,
            ApprovalDecisionSource.HOST_LOOPBACK.value,
        }:
            session_id = None
        if outcome is HumanDecisionOutcome.APPROVED:
            reason = decision.reason or (
                f"{source} session={session_id}" if session_id else source
            )
            self._approvals.approve(
                approval_id,
                reason=reason,
                decision_source=source,
                session_id=session_id,
            )
            return run(approval_id, session_id)
        if outcome is HumanDecisionOutcome.DENIED:
            reason = decision.reason or (
                f"{source}_declined session={session_id}" if session_id else f"{source}_declined"
            )
            self._approvals.deny(
                approval_id,
                reason=reason,
                decision_source=source,
                session_id=session_id,
            )
            raise ExecutionApprovalDeniedError(approval_id)
        action = {
            HumanDecisionOutcome.CANCELLED: "cancel",
            HumanDecisionOutcome.TIMED_OUT: "timeout",
            HumanDecisionOutcome.UNAVAILABLE: "unavailable",
        }.get(outcome, outcome.value)
        required.details["elicitation_action"] = action
        raise required
