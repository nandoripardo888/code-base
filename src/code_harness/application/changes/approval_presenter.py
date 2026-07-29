from __future__ import annotations

from code_harness.domain.enums import ExecutionRiskSeverity, HumanDecisionOutcome
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionDiff
from code_harness.domain.models.execution import RiskFinding
from code_harness.domain.models.human_decision import (
    DecisionDetail,
    DecisionOption,
    HumanDecisionRequest,
)


class ChangeSessionApprovalPresenter:
    def present(
        self,
        session: ChangeSession,
        *,
        diff: ChangeSessionDiff | None = None,
        approval_id: str,
        expires_at: str,
    ) -> HumanDecisionRequest:
        files = diff.files if diff is not None else ()
        details = (
            DecisionDetail("session_id", "Session", session.session_id),
            DecisionDetail("topology", "Topology", str(session.topology_kind)),
            DecisionDetail(
                "digest",
                "Candidate digest",
                session.candidate_digest or "",
            ),
            DecisionDetail("segments", "Segments", str(len(session.segments))),
            DecisionDetail("files", "Changed files", ", ".join(files[:20]) or "(none)"),
        )
        risks: list[RiskFinding] = []
        if len(session.segments) > 1:
            risks.append(
                RiskFinding(
                    code="non_atomic_multi_repo",
                    severity=ExecutionRiskSeverity.MEDIUM,
                    message="Integration across multiple repositories is not atomic.",
                )
            )
        summary = (
            f"Apply isolated change session {session.session_id[:8]} "
            f"({len(files)} file(s), {len(session.segments)} segment(s))."
        )
        return HumanDecisionRequest(
            request_id=approval_id,
            subject_kind="change_session_accept",
            subject_digest=session.candidate_digest or "",
            title="Confirm change session integration",
            summary=summary,
            details=details,
            risks=tuple(risks),
            options=(
                DecisionOption("approve", "Approve once", HumanDecisionOutcome.APPROVED),
                DecisionOption("decline", "Decline", HumanDecisionOutcome.DENIED),
            ),
            expires_at=expires_at,
        )
