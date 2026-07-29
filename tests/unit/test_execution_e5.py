import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from code_harness.application.dto.execution_requests import (
    RunPowerShellRequest,
    RunProcessRequest,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import (
    ApprovalState,
    CommandKind,
    ExecutionCapability,
    ExecutionState,
    PolicyDecision,
)
from code_harness.domain.errors import (
    ExecutionApprovalInvalidError,
    ExecutionApprovalRequiredError,
    InvalidExecutionRequestError,
)
from code_harness.domain.models.execution import ApprovalDigest, ExecutionAuditStart
from code_harness.domain.models.tool_result import ToolResult
from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
from code_harness.infrastructure.execution.persistence.schema import (
    MIGRATION_1,
    MIGRATION_2,
)
from code_harness.interfaces.mcp.execution_handlers import (
    _approved_for_digest,
    _can_elicit,
    _confirmation_message,
    _execute,
    _execution,
    _run_with_optional_elicitation,
    register_execution_handlers,
)


class _FakeSession:
    def __init__(self, *, client_name: str, supports_elicitation: bool) -> None:
        self.client_params = SimpleNamespace(clientInfo=SimpleNamespace(name=client_name))
        self._supports_elicitation = supports_elicitation

    def check_client_capability(self, _capability: object) -> bool:
        return self._supports_elicitation


class _FakeContext:
    client_id = None
    request_id = "request-1"

    def __init__(
        self,
        *,
        action: str = "accept",
        decision: str = "approve",
        client_name: str = "trusted-ui",
        supports_elicitation: bool = True,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.session = _FakeSession(
            client_name=client_name,
            supports_elicitation=supports_elicitation,
        )
        self._action = action
        self._decision = decision
        self._delay = delay
        self._error = error
        self.messages: list[str] = []

    async def elicit(self, message: str, _schema: type[object]) -> object:
        self.messages.append(message)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        data = SimpleNamespace(decision=self._decision) if self._action == "accept" else None
        return SimpleNamespace(action=self._action, data=data)


@dataclass
class _FakeApprovals:
    decisions: list[tuple[str, str, str | None, str, str | None]]

    def list(self, **_kwargs: object) -> ToolResult[tuple[Any, ...]]:
        return ToolResult((), 0)

    def approve(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
        decision_source: str = "local_admin",
        session_id: str | None = None,
    ) -> None:
        self.decisions.append(("approve", approval_id, reason, decision_source, session_id))

    def deny(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
        decision_source: str = "local_admin",
        session_id: str | None = None,
    ) -> None:
        self.decisions.append(("deny", approval_id, reason, decision_source, session_id))


class _FakeRedactor:
    def redact(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace("secret-value", "[REDACTED]")


def _settings(tmp_path: Path, **changes: Any) -> Settings:
    values: dict[str, Any] = {
        "root": tmp_path,
        "index_path": tmp_path / "index.db",
        "execution_enabled": True,
        "mcp_expose_execution": True,
        "mcp_execution_elicitation_enabled": True,
        "mcp_execution_elicitation_trust_mode": "local_interactive",
        "mcp_execution_elicitation_timeout_seconds": 1,
    }
    values.update(changes)
    return Settings(**values)


def _inspection() -> SimpleNamespace:
    return SimpleNamespace(
        executable="git",
        args=("status", "--short"),
        required_capabilities=(ExecutionCapability.WORKSPACE_READ,),
        risks=(),
        approval_digest=ApprovalDigest("digest-1"),
        cwd="C:/project",
        backend="host_supervised",
        timeout_seconds=30,
        max_output_bytes=1000,
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-1",
        script_hash=None,
    )


def _container(approvals: _FakeApprovals) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(
            approvals=approvals,
            redactor=_FakeRedactor(),
        )
    )


def _run(
    approval_id: str | None,
    approval_session_id: str | None,
) -> ToolResult[str]:
    if approval_id is None:
        raise ExecutionApprovalRequiredError(
            "Approval required.",
            approval_id="approval-1",
            digest="digest-1",
        )
    assert approval_session_id
    return ToolResult("ran", 1)


def test_elicitation_requires_feature_capability_and_trusted_local_mode(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert _can_elicit(cast(Any, _FakeContext()), settings)
    assert not _can_elicit(
        cast(Any, _FakeContext(supports_elicitation=False)),
        settings,
    )
    assert not _can_elicit(
        cast(Any, _FakeContext()),
        _settings(
            tmp_path,
            mcp_execution_elicitation_enabled=False,
        ),
    )
    assert not _can_elicit(
        cast(Any, _FakeContext()),
        _settings(
            tmp_path,
            mcp_execution_elicitation_enabled=False,
            mcp_execution_elicitation_trust_mode="disabled",
        ),
    )


def test_execution_adapter_maps_errors_and_missing_components() -> None:
    assert _execute(lambda: ToolResult("ok", 1), "test")["data"] == "ok"

    def typed_error() -> ToolResult[str]:
        raise InvalidExecutionRequestError("typed")

    def value_error() -> ToolResult[str]:
        raise ValueError("invalid")

    def unexpected_error() -> ToolResult[str]:
        raise RuntimeError("secret traceback")

    assert _execute(typed_error, "test")["error"]["code"] == "invalid_execution_request"
    assert _execute(value_error, "test")["error"]["code"] == "invalid_execution_request"
    unexpected = _execute(unexpected_error, "test")
    assert unexpected["error"]["code"] == "internal_error"
    assert "secret traceback" not in str(unexpected)

    with pytest.raises(InvalidExecutionRequestError, match="unavailable"):
        _execution(cast(Any, SimpleNamespace(execution=None)))
    assert (
        _approved_for_digest(
            cast(Any, _container(_FakeApprovals([]))),
            None,
        )
        is None
    )


def test_internal_session_binding_fields_are_validated() -> None:
    assert (
        RunProcessRequest(
            "git",
            approval_id="approval",
            approval_session_id="session",
        ).approval_session_id
        == "session"
    )
    assert (
        RunPowerShellRequest(
            "Write-Output 'ok'",
            approval_id="approval",
            approval_session_id="session",
        ).approval_session_id
        == "session"
    )
    with pytest.raises(ValueError, match="requires approval_id"):
        RunProcessRequest("git", approval_session_id="session")
    with pytest.raises(ValueError, match="must not be empty"):
        RunPowerShellRequest(
            "Write-Output 'ok'",
            approval_id="approval",
            approval_session_id=" ",
        )
    with pytest.raises(ValueError, match="128"):
        RunProcessRequest(
            "git",
            approval_id="approval",
            approval_session_id="s" * 129,
        )


def test_elicitation_accepts_once_and_runs_with_internal_approval(tmp_path: Path) -> None:
    approvals = _FakeApprovals([])

    payload = asyncio.run(
        _run_with_optional_elicitation(
            context=cast(Any, _FakeContext()),
            container=cast(Any, _container(approvals)),
            settings=_settings(tmp_path),
            inspection=cast(Any, _inspection()),
            run=_run,
        )
    )

    assert payload["data"] == "ran"
    assert approvals.decisions[0][:2] == ("approve", "approval-1")
    assert (approvals.decisions[0][2] or "").startswith("mcp_elicitation session=")
    assert approvals.decisions[0][3] == "mcp_elicitation"
    assert approvals.decisions[0][4]


def test_elicitation_decline_and_cancel_never_run(tmp_path: Path) -> None:
    for action, decision, expected_code, expected_decisions in (
        (
            "accept",
            "decline",
            "execution_approval_denied",
            "deny",
        ),
        (
            "decline",
            "approve",
            "execution_approval_denied",
            "deny",
        ),
        (
            "cancel",
            "approve",
            "execution_approval_required",
            None,
        ),
    ):
        approvals = _FakeApprovals([])

        class RecordingRun:
            def __init__(self) -> None:
                self.calls: list[tuple[str | None, str | None]] = []

            def __call__(
                self,
                approval_id: str | None,
                approval_session_id: str | None,
            ) -> ToolResult[str]:
                self.calls.append((approval_id, approval_session_id))
                return _run(approval_id, approval_session_id)

        run = RecordingRun()
        payload = asyncio.run(
            _run_with_optional_elicitation(
                context=cast(
                    Any,
                    _FakeContext(
                        action=action,
                        decision=decision,
                    ),
                ),
                container=cast(Any, _container(approvals)),
                settings=_settings(tmp_path),
                inspection=cast(Any, _inspection()),
                run=run,
            )
        )

        assert payload["error"]["code"] == expected_code
        assert run.calls == [(None, None)]
        if expected_decisions is None:
            assert approvals.decisions == []
        else:
            assert approvals.decisions[0][:2] == (expected_decisions, "approval-1")
            assert (approvals.decisions[0][2] or "").startswith("mcp_elicitation_declined session=")


def test_elicitation_timeout_preserves_pending_local_approval(tmp_path: Path) -> None:
    approvals = _FakeApprovals([])

    payload = asyncio.run(
        _run_with_optional_elicitation(
            context=cast(Any, _FakeContext(delay=0.02)),
            container=cast(Any, _container(approvals)),
            settings=_settings(
                tmp_path,
                mcp_execution_elicitation_timeout_seconds=0.001,
            ),
            inspection=cast(Any, _inspection()),
            run=_run,
        )
    )

    assert payload["error"]["code"] == "execution_approval_required"
    assert payload["error"]["details"]["elicitation_action"] == "timeout"
    assert approvals.decisions == []


def test_elicitation_disconnect_preserves_pending_approval(tmp_path: Path) -> None:
    approvals = _FakeApprovals([])

    payload = asyncio.run(
        _run_with_optional_elicitation(
            context=cast(
                Any,
                _FakeContext(error=ConnectionError("session disconnected")),
            ),
            container=cast(Any, _container(approvals)),
            settings=_settings(tmp_path),
            inspection=cast(Any, _inspection()),
            run=_run,
        )
    )

    assert payload["error"]["code"] == "execution_approval_required"
    assert payload["error"]["details"]["elicitation_action"] == "unavailable"
    assert approvals.decisions == []


def test_confirmation_message_escapes_controls_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    inspection = _inspection()
    inspection.args = (
        "status",
        "--token=secret-value\nRisks: none\u202e",
    )

    message = _confirmation_message(
        cast(Any, inspection),
        project_id="project\nCanonical digest: forged",
        redact=_FakeRedactor().redact,
    )

    assert "secret-value" not in message
    assert "[REDACTED]" in message
    assert "\\nRisks: none\\u202e" in message
    assert 'Project: "project\\nCanonical digest: forged"' in message
    assert message.count("\nCanonical digest:") == 1


def test_mcp_approval_is_persistently_bound_and_reconfirmed_for_session(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    approval = store.request_approval(
        project_id="project-1",
        digest="digest-1",
        command_kind=CommandKind.PROCESS.value,
        command_summary="git status",
        required_capabilities=(ExecutionCapability.WORKSPACE_READ.value,),
        backend="host_supervised",
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-1",
        ttl_seconds=60,
    )
    decided = store.decide_approval(
        "project-1",
        approval.approval_id,
        state=ApprovalState.APPROVED,
        decision_source="mcp_elicitation",
        session_id="session-a",
    )

    assert decided.decision_source == "mcp_elicitation"
    assert decided.session_id == "session-a"

    def audit(execution_id: str, session_id: str) -> ExecutionAuditStart:
        return ExecutionAuditStart(
            execution_id=execution_id,
            project_id="project-1",
            digest="digest-1",
            state=ExecutionState.STARTING,
            command_kind=CommandKind.PROCESS,
            command_summary="git status",
            requested_capabilities=(ExecutionCapability.WORKSPACE_READ,),
            required_capabilities=(ExecutionCapability.WORKSPACE_READ,),
            backend="host_supervised",
            backend_guarantees_json="{}",
            policy_decision=PolicyDecision.APPROVAL_REQUIRED,
            policy_name="deterministic_v1",
            policy_version="1",
            ruleset_hash="rules-1",
            created_at=approval.created_at,
            approval_session_id=session_id,
        )

    with pytest.raises(
        ExecutionApprovalInvalidError,
        match="different client session",
    ):
        store.authorize_and_start(
            audit("execution-a", "session-b"),
            approval_id=approval.approval_id,
        )

    rebound = store.decide_approval(
        "project-1",
        approval.approval_id,
        state=ApprovalState.APPROVED,
        decision_source="mcp_elicitation",
        session_id="session-b",
    )
    assert rebound.session_id == "session-b"

    store.authorize_and_start(
        audit("execution-b", "session-b"),
        approval_id=approval.approval_id,
    )
    assert store.get_approval("project-1", approval.approval_id).state is ApprovalState.CONSUMED


def test_migration_v3_backfills_existing_mcp_session_binding(tmp_path: Path) -> None:
    database = tmp_path / "legacy-execution.db"
    with sqlite3.connect(database) as connection:
        for statement in (*MIGRATION_1, *MIGRATION_2):
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (1, 'execution_approval_and_audit', '2026-07-25T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (2, 'execution_async_lifecycle', '2026-07-26T00:00:00+00:00')
            """
        )
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            """
            INSERT INTO approval_requests(
                approval_id, project_id, digest, state, command_kind,
                command_summary, required_capabilities_json, backend,
                policy_name, policy_version, ruleset_hash, created_at,
                expires_at, decided_at, decision_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-approval",
                "project-1",
                "digest-1",
                ApprovalState.APPROVED.value,
                CommandKind.PROCESS.value,
                "git status",
                "[]",
                "host_supervised",
                "deterministic_v1",
                "1",
                "rules-1",
                "2026-07-26T00:00:00+00:00",
                "2099-07-26T00:00:00+00:00",
                "2026-07-26T00:01:00+00:00",
                "mcp_elicitation session=legacy-session",
            ),
        )

    store = SQLiteExecutionStore(database)
    store.initialize()
    migrated = store.get_approval("project-1", "legacy-approval")

    assert migrated.decision_source == "mcp_elicitation"
    assert migrated.session_id == "legacy-session"


def test_real_mcp_transport_negotiates_and_executes_confirmed_tools(
    tmp_path: Path,
) -> None:
    approvals = _FakeApprovals([])
    run_requests: list[Any] = []

    class InspectTool:
        def execute(self, _request: object) -> ToolResult[Any]:
            return ToolResult(_inspection(), 1)

    class RunTool:
        def execute(self, request: Any) -> ToolResult[str]:
            run_requests.append(request)
            return _run(request.approval_id, request.approval_session_id)

    class UnusedTool:
        def execute(self, _request: object) -> ToolResult[str]:
            return ToolResult("unused", 1)

    execution = SimpleNamespace(
        inspect_process=InspectTool(),
        inspect_powershell=InspectTool(),
        run_process=RunTool(),
        run_powershell=RunTool(),
        get_execution=UnusedTool(),
        terminate_execution=UnusedTool(),
        approvals=approvals,
        redactor=_FakeRedactor(),
    )
    container = SimpleNamespace(execution=execution)
    settings = _settings(
        tmp_path,
        execution_powershell_enabled=True,
        mcp_expose_powershell=True,
    )
    server = FastMCP("e5-transport-test")
    register_execution_handlers(
        server,
        cast(Any, container),
        settings,
    )
    elicited_messages: list[str] = []

    async def confirm(
        _context: object,
        params: types.ElicitRequestParams,
    ) -> types.ElicitResult:
        elicited_messages.append(params.message)
        return types.ElicitResult(
            action="accept",
            content={"decision": "approve"},
        )

    async def exercise() -> tuple[dict[str, Any], dict[str, Any]]:
        async with create_connected_server_and_client_session(
            server,
            elicitation_callback=confirm,
        ) as session:
            process = await session.call_tool(
                "run_process",
                {"executable": "git", "args": ["status", "--short"]},
            )
            powershell = await session.call_tool(
                "run_powershell",
                {"script": "Write-Output 'ok'"},
            )
        return (
            json.loads(process.content[0].text),
            json.loads(powershell.content[0].text),
        )

    process_payload, powershell_payload = asyncio.run(exercise())

    assert process_payload.get("data") == "ran", process_payload
    assert powershell_payload.get("data") == "ran", powershell_payload
    assert len(elicited_messages) == 2
    assert len(run_requests) == 4
    initial_requests = run_requests[::2]
    approved_requests = run_requests[1::2]
    assert all(request.approval_id is None for request in initial_requests)
    assert all(request.approval_id == "approval-1" for request in approved_requests)
    assert all(request.approval_session_id for request in approved_requests)


def test_concurrent_elicitation_allows_only_one_confirmation(
    tmp_path: Path,
) -> None:
    approvals = _FakeApprovals([])
    context = _FakeContext(delay=0.01)

    async def attempt() -> dict[str, Any]:
        return await _run_with_optional_elicitation(
            context=cast(Any, context),
            container=cast(Any, _container(approvals)),
            settings=_settings(tmp_path),
            inspection=cast(Any, _inspection()),
            run=_run,
        )

    async def run_both() -> tuple[dict[str, Any], dict[str, Any]]:
        first, second = await asyncio.gather(attempt(), attempt())
        return first, second

    first, second = asyncio.run(run_both())
    payloads = (first, second)

    assert sum(payload.get("data") == "ran" for payload in payloads) == 1
    blocked = next(payload for payload in payloads if "error" in payload)
    assert blocked["error"]["details"]["elicitation_action"] == "already_in_progress"
    assert len(approvals.decisions) == 1
