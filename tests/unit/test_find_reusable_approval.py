from pathlib import Path

from code_harness.application.execution.approval_admin import ApprovalAdminTool
from code_harness.domain.enums import (
    ApprovalDecisionSource,
    ApprovalState,
    CommandKind,
    ExecutionCapability,
)
from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
from code_harness.infrastructure.execution.redaction import SensitiveDataRedactor


def test_find_reusable_approval_queries_digest_and_source(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    project_id = "project-1"
    approval = store.request_approval(
        project_id=project_id,
        digest="digest-reusable",
        command_kind=CommandKind.PROCESS.value,
        command_summary="git status",
        required_capabilities=(ExecutionCapability.WORKSPACE_READ.value,),
        backend="host_supervised",
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-1",
        ttl_seconds=600,
    )
    store.decide_approval(
        project_id,
        approval.approval_id,
        state=ApprovalState.APPROVED,
        decision_source=ApprovalDecisionSource.LOCAL_ADMIN.value,
    )
    mcp = store.request_approval(
        project_id=project_id,
        digest="digest-mcp",
        command_kind=CommandKind.PROCESS.value,
        command_summary="git status",
        required_capabilities=(ExecutionCapability.WORKSPACE_READ.value,),
        backend="host_supervised",
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-1",
        ttl_seconds=600,
    )
    store.decide_approval(
        project_id,
        mcp.approval_id,
        state=ApprovalState.APPROVED,
        decision_source=ApprovalDecisionSource.MCP_ELICITATION.value,
        session_id="session-1",
    )

    found = store.find_reusable_approval(
        project_id,
        digest="digest-reusable",
        allowed_sources=(ApprovalDecisionSource.LOCAL_ADMIN.value,),
    )
    skipped = store.find_reusable_approval(
        project_id,
        digest="digest-mcp",
        allowed_sources=(ApprovalDecisionSource.LOCAL_ADMIN.value,),
    )
    admin = ApprovalAdminTool(
        project_id=project_id,
        store=store,
        redact=SensitiveDataRedactor(str(tmp_path)),
    )

    assert found is not None
    assert found.approval_id == approval.approval_id
    assert skipped is None
    assert (
        admin.find_reusable(
            digest="digest-reusable",
            allowed_sources=(ApprovalDecisionSource.LOCAL_ADMIN.value,),
        )
        is not None
    )
