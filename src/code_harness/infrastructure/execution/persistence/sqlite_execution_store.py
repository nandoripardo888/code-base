from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from code_harness.domain.enums import (
    ApprovalState,
    CommandKind,
    ExecutionCapability,
    ExecutionState,
)
from code_harness.domain.errors import (
    ExecutionApprovalConsumedError,
    ExecutionApprovalDeniedError,
    ExecutionApprovalExpiredError,
    ExecutionApprovalInvalidError,
    ExecutionApprovalNotFoundError,
    ExecutionStoreUnavailableError,
)
from code_harness.domain.models.execution import ExecutionApproval, ExecutionAuditStart
from code_harness.infrastructure.execution.persistence.migrations import (
    apply_execution_migrations,
)
from code_harness.infrastructure.persistence.connection import connect_database


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class SQLiteExecutionStore:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self.path = path
        self._clock = clock

    def initialize(self) -> None:
        apply_execution_migrations(self.path)

    def request_approval(
        self,
        *,
        project_id: str,
        digest: str,
        command_kind: str,
        command_summary: str,
        required_capabilities: tuple[str, ...],
        backend: str,
        policy_name: str,
        policy_version: str,
        ruleset_hash: str,
        ttl_seconds: int,
    ) -> ExecutionApproval:
        now = self._clock()
        now_text = now.isoformat()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_due(connection, project_id, now_text)
                row = connection.execute(
                    """
                    SELECT * FROM approval_requests
                    WHERE project_id = ? AND digest = ?
                      AND (
                        state IN ('pending', 'approved')
                        OR (state = 'denied' AND expires_at > ?)
                      )
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (project_id, digest, now_text),
                ).fetchone()
                if row is not None:
                    return self._approval_from_row(row)
                approval_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO approval_requests(
                        approval_id, project_id, digest, state, command_kind,
                        command_summary, required_capabilities_json, backend,
                        policy_name, policy_version, ruleset_hash, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        project_id,
                        digest,
                        ApprovalState.PENDING.value,
                        command_kind,
                        command_summary,
                        _json(required_capabilities),
                        backend,
                        policy_name,
                        policy_version,
                        ruleset_hash,
                        now_text,
                        expires_at,
                    ),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    approval_id=approval_id,
                    event_type="approval_requested",
                    occurred_at=now_text,
                    details={"digest": digest},
                )
                row = connection.execute(
                    "SELECT * FROM approval_requests WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                assert row is not None
                return self._approval_from_row(row)
        except (
            ExecutionApprovalNotFoundError,
            ExecutionApprovalInvalidError,
            ExecutionStoreUnavailableError,
        ):
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def list_approvals(
        self,
        project_id: str,
        *,
        state: ApprovalState | None = None,
        limit: int = 50,
    ) -> tuple[ExecutionApproval, ...]:
        now_text = self._clock().isoformat()
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_due(connection, project_id, now_text)
                if state is None:
                    rows = connection.execute(
                        """
                        SELECT * FROM approval_requests
                        WHERE project_id = ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (project_id, limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM approval_requests
                        WHERE project_id = ? AND state = ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (project_id, state.value, limit),
                    ).fetchall()
                return tuple(self._approval_from_row(row) for row in rows)
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def get_approval(self, project_id: str, approval_id: str) -> ExecutionApproval:
        now_text = self._clock().isoformat()
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_due(connection, project_id, now_text)
                row = connection.execute(
                    """
                    SELECT * FROM approval_requests
                    WHERE project_id = ? AND approval_id = ?
                    """,
                    (project_id, approval_id),
                ).fetchone()
                if row is None:
                    raise ExecutionApprovalNotFoundError(approval_id)
                return self._approval_from_row(row)
        except ExecutionApprovalNotFoundError:
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def decide_approval(
        self,
        project_id: str,
        approval_id: str,
        *,
        state: ApprovalState,
        reason: str | None = None,
    ) -> ExecutionApproval:
        if state not in {ApprovalState.APPROVED, ApprovalState.DENIED}:
            raise ExecutionApprovalInvalidError(
                "Approval decisions must be approved or denied.",
                approval_id=approval_id,
                state=state.value,
            )
        now_text = self._clock().isoformat()
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_due(connection, project_id, now_text)
                row = connection.execute(
                    """
                    SELECT * FROM approval_requests
                    WHERE project_id = ? AND approval_id = ?
                    """,
                    (project_id, approval_id),
                ).fetchone()
                if row is None:
                    raise ExecutionApprovalNotFoundError(approval_id)
                current = ApprovalState(str(row["state"]))
                if current is state:
                    return self._approval_from_row(row)
                if current is ApprovalState.EXPIRED:
                    raise ExecutionApprovalExpiredError(approval_id)
                if current is ApprovalState.CONSUMED:
                    raise ExecutionApprovalConsumedError(approval_id)
                if current is not ApprovalState.PENDING:
                    raise ExecutionApprovalInvalidError(
                        "Approval has already received a conflicting decision.",
                        approval_id=approval_id,
                        state=current.value,
                    )
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET state = ?, decided_at = ?, decision_reason = ?
                    WHERE approval_id = ? AND state = 'pending'
                    """,
                    (state.value, now_text, reason, approval_id),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    approval_id=approval_id,
                    event_type=f"approval_{state.value}",
                    occurred_at=now_text,
                    details={"reason": reason} if reason else {},
                )
                updated = connection.execute(
                    "SELECT * FROM approval_requests WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                assert updated is not None
                return self._approval_from_row(updated)
        except (
            ExecutionApprovalConsumedError,
            ExecutionApprovalExpiredError,
            ExecutionApprovalInvalidError,
            ExecutionApprovalNotFoundError,
        ):
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def authorize_and_start(
        self,
        start: ExecutionAuditStart,
        *,
        approval_id: str | None,
    ) -> None:
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if approval_id is not None:
                    self._consume_approval(connection, start, approval_id)
                self._insert_execution(connection, start, approval_id=approval_id)
                self._event(
                    connection,
                    project_id=start.project_id,
                    execution_id=start.execution_id,
                    approval_id=approval_id,
                    event_type="execution_started",
                    occurred_at=start.created_at,
                )
        except (
            ExecutionApprovalConsumedError,
            ExecutionApprovalDeniedError,
            ExecutionApprovalExpiredError,
            ExecutionApprovalInvalidError,
            ExecutionApprovalNotFoundError,
        ):
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def record_blocked(self, start: ExecutionAuditStart, *, reason: str) -> None:
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._insert_execution(connection, start, approval_id=None)
                connection.execute(
                    """
                    UPDATE executions
                    SET finished_at = ?, error_code = ?, error_message = ?
                    WHERE execution_id = ?
                    """,
                    (start.created_at, "execution_policy_denied", reason, start.execution_id),
                )
                self._event(
                    connection,
                    project_id=start.project_id,
                    execution_id=start.execution_id,
                    event_type="execution_blocked",
                    occurred_at=start.created_at,
                    details={"reason": reason},
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def finish_execution(
        self,
        execution_id: str,
        *,
        state: ExecutionState,
        finished_at: str,
        elapsed_ms: int,
        exit_code: int | None,
        stdout_bytes: int,
        stderr_bytes: int,
        stdout_sha256: str | None,
        stderr_sha256: str | None,
        stdout_truncated: bool,
        stderr_truncated: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            with connect_database(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE executions SET
                        state = ?, finished_at = ?, elapsed_ms = ?, exit_code = ?,
                        stdout_bytes = ?, stderr_bytes = ?,
                        stdout_sha256 = ?, stderr_sha256 = ?,
                        stdout_truncated = ?, stderr_truncated = ?,
                        error_code = ?, error_message = ?
                    WHERE execution_id = ?
                    """,
                    (
                        state.value,
                        finished_at,
                        elapsed_ms,
                        exit_code,
                        stdout_bytes,
                        stderr_bytes,
                        stdout_sha256,
                        stderr_sha256,
                        int(stdout_truncated),
                        int(stderr_truncated),
                        error_code,
                        error_message,
                        execution_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ExecutionStoreUnavailableError(
                        "Execution audit record disappeared before completion."
                    )
                row = connection.execute(
                    "SELECT project_id FROM executions WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                assert row is not None
                self._event(
                    connection,
                    project_id=str(row["project_id"]),
                    execution_id=execution_id,
                    event_type=f"execution_{state.value}",
                    occurred_at=finished_at,
                    details={"error_code": error_code} if error_code else {},
                )
        except ExecutionStoreUnavailableError:
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ExecutionStoreUnavailableError() from error

    def _consume_approval(
        self,
        connection: sqlite3.Connection,
        start: ExecutionAuditStart,
        approval_id: str,
    ) -> None:
        self._expire_due(connection, start.project_id, start.created_at)
        row = connection.execute(
            """
            SELECT * FROM approval_requests
            WHERE project_id = ? AND approval_id = ?
            """,
            (start.project_id, approval_id),
        ).fetchone()
        if row is None:
            raise ExecutionApprovalNotFoundError(approval_id)
        state = ApprovalState(str(row["state"]))
        if state is ApprovalState.EXPIRED:
            raise ExecutionApprovalExpiredError(approval_id)
        if state is ApprovalState.CONSUMED:
            raise ExecutionApprovalConsumedError(approval_id)
        if state is ApprovalState.DENIED:
            raise ExecutionApprovalDeniedError(approval_id)
        if state is not ApprovalState.APPROVED:
            raise ExecutionApprovalInvalidError(
                "Execution approval is still pending.",
                approval_id=approval_id,
                state=state.value,
            )
        if str(row["digest"]) != (start.digest or ""):
            raise ExecutionApprovalInvalidError(
                "Approval digest does not match the inspected command.",
                approval_id=approval_id,
            )
        cursor = connection.execute(
            """
            UPDATE approval_requests
            SET state = 'consumed', consumed_at = ?
            WHERE approval_id = ? AND state = 'approved'
            """,
            (start.created_at, approval_id),
        )
        if cursor.rowcount != 1:
            raise ExecutionApprovalConsumedError(approval_id)
        self._event(
            connection,
            project_id=start.project_id,
            approval_id=approval_id,
            event_type="approval_consumed",
            occurred_at=start.created_at,
            details={"execution_id": start.execution_id},
        )

    @staticmethod
    def _insert_execution(
        connection: sqlite3.Connection,
        start: ExecutionAuditStart,
        *,
        approval_id: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO executions(
                execution_id, project_id, digest, approval_id, state,
                command_kind, command_summary, backend, backend_guarantees_json,
                policy_decision, policy_name, policy_version, ruleset_hash,
                created_at, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                start.execution_id,
                start.project_id,
                start.digest,
                approval_id,
                start.state.value,
                start.command_kind.value,
                start.command_summary,
                start.backend,
                start.backend_guarantees_json,
                start.policy_decision.value,
                start.policy_name,
                start.policy_version,
                start.ruleset_hash,
                start.created_at,
                start.created_at if start.state is ExecutionState.STARTING else None,
            ),
        )
        rows = (
            *((item.value, "requested") for item in start.requested_capabilities),
            *((item.value, "required") for item in start.required_capabilities),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO execution_capabilities(execution_id, capability, source)
            VALUES (?, ?, ?)
            """,
            ((start.execution_id, capability, source) for capability, source in rows),
        )

    def _expire_due(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        now_text: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT approval_id FROM approval_requests
            WHERE project_id = ? AND state IN ('pending', 'approved') AND expires_at <= ?
            """,
            (project_id, now_text),
        ).fetchall()
        if not rows:
            return
        connection.execute(
            """
            UPDATE approval_requests
            SET state = 'expired'
            WHERE project_id = ? AND state IN ('pending', 'approved') AND expires_at <= ?
            """,
            (project_id, now_text),
        )
        for row in rows:
            self._event(
                connection,
                project_id=project_id,
                approval_id=str(row["approval_id"]),
                event_type="approval_expired",
                occurred_at=now_text,
            )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        event_type: str,
        occurred_at: str,
        execution_id: str | None = None,
        approval_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO execution_events(
                project_id, execution_id, approval_id, event_type, occurred_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                execution_id,
                approval_id,
                event_type,
                occurred_at,
                _json(details or {}),
            ),
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ExecutionApproval:
        return ExecutionApproval(
            approval_id=str(row["approval_id"]),
            project_id=str(row["project_id"]),
            digest=str(row["digest"]),
            state=ApprovalState(str(row["state"])),
            command_kind=CommandKind(str(row["command_kind"])),
            command_summary=str(row["command_summary"]),
            required_capabilities=tuple(
                ExecutionCapability(item)
                for item in json.loads(str(row["required_capabilities_json"]))
            ),
            backend=str(row["backend"]),
            policy_name=str(row["policy_name"]),
            policy_version=str(row["policy_version"]),
            ruleset_hash=str(row["ruleset_hash"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            decided_at=str(row["decided_at"]) if row["decided_at"] is not None else None,
            consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
            decision_reason=(
                str(row["decision_reason"]) if row["decision_reason"] is not None else None
            ),
        )
