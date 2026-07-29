from __future__ import annotations

import json
from collections.abc import Callable

from code_harness.domain.enums import CommandKind, HumanDecisionOutcome
from code_harness.domain.models.execution import CommandInspection
from code_harness.domain.models.human_decision import (
    DecisionDetail,
    DecisionOption,
    HumanDecisionRequest,
)


class ExecutionApprovalPresenter:
    """Maps execution inspections into generic human-decision requests."""

    def __init__(self, *, redact: Callable[[str | None], str | None]) -> None:
        self._redact = redact

    def present(
        self,
        inspection: CommandInspection,
        *,
        project_id: str,
        approval_id: str,
        expires_at: str,
    ) -> HumanDecisionRequest:
        digest = inspection.approval_digest.value if inspection.approval_digest else ""
        subject_kind = (
            "powershell_execution"
            if inspection.kind is CommandKind.POWERSHELL
            else "process_execution"
        )
        details = (
            DecisionDetail("project_id", "Project", self._safe(project_id)),
            DecisionDetail("command", "Command", self._command_value(inspection)),
            DecisionDetail("cwd", "Working directory", self._safe(inspection.cwd)),
            DecisionDetail("backend", "Backend", self._safe(inspection.backend)),
            DecisionDetail(
                "capabilities",
                "Capabilities",
                self._safe(
                    ", ".join(item.value for item in inspection.required_capabilities) or "none"
                ),
            ),
            DecisionDetail(
                "limits",
                "Limits",
                self._safe(
                    f"timeout={inspection.timeout_seconds}s, "
                    f"output={inspection.max_output_bytes} bytes"
                ),
            ),
            DecisionDetail(
                "policy",
                "Policy",
                self._safe(
                    f"{inspection.policy_name}/{inspection.policy_version} "
                    f"ruleset={inspection.ruleset_hash}"
                ),
            ),
            DecisionDetail("digest", "Canonical digest", self._safe(digest)),
        )
        return HumanDecisionRequest(
            request_id=approval_id,
            subject_kind=subject_kind,
            subject_digest=digest,
            title="Confirm a one-time supervised execution",
            summary=self.format_message(
                inspection,
                project_id=project_id,
            ),
            details=details,
            risks=inspection.risks,
            options=(
                DecisionOption("approve", "Approve once", HumanDecisionOutcome.APPROVED),
                DecisionOption("decline", "Decline", HumanDecisionOutcome.DENIED),
            ),
            expires_at=expires_at,
        )

    def format_message(
        self,
        inspection: CommandInspection,
        *,
        project_id: str,
    ) -> str:
        capabilities = ", ".join(item.value for item in inspection.required_capabilities) or "none"
        risks = "; ".join(f"{item.severity.value}: {item.message}" for item in inspection.risks)
        digest = inspection.approval_digest.value if inspection.approval_digest else ""
        policy = self._safe(f"{inspection.policy_name}/{inspection.policy_version}")
        return (
            "Confirm a one-time supervised execution.\n"
            f"Project: {json.dumps(self._safe(project_id), ensure_ascii=True)}\n"
            f"Command: {self._command_json(inspection)}\n"
            f"Working directory: {json.dumps(self._safe(inspection.cwd), ensure_ascii=True)}\n"
            f"Backend: {json.dumps(self._safe(inspection.backend), ensure_ascii=True)}\n"
            f"Capabilities: {json.dumps(self._safe(capabilities), ensure_ascii=True)}\n"
            f"Limits: timeout={inspection.timeout_seconds}s, "
            f"output={inspection.max_output_bytes} bytes\n"
            f"Risks: {json.dumps(self._safe(risks or 'none reported'), ensure_ascii=True)}\n"
            f"Policy: {json.dumps(policy, ensure_ascii=True)} "
            f"ruleset={json.dumps(self._safe(inspection.ruleset_hash), ensure_ascii=True)}\n"
            f"Canonical digest: {json.dumps(self._safe(digest), ensure_ascii=True)}"
        )

    def _command_value(self, inspection: CommandInspection) -> str:
        if inspection.executable:
            parts = [
                self._safe(inspection.executable),
                *(self._safe(argument) for argument in inspection.args),
            ]
            return " ".join(parts)
        return self._safe(f"PowerShell script sha256:{inspection.script_hash}")

    def _command_json(self, inspection: CommandInspection) -> str:
        if inspection.executable:
            return json.dumps(
                [
                    self._safe(inspection.executable),
                    *(self._safe(argument) for argument in inspection.args),
                ],
                ensure_ascii=True,
            )
        return json.dumps(
            self._safe(f"PowerShell script sha256:{inspection.script_hash}"),
            ensure_ascii=True,
        )

    def _safe(self, value: str | None) -> str:
        return self._redact(value) or ""
