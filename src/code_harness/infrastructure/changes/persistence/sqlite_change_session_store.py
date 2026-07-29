from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from code_harness.domain.enums import (
    ChangeSegmentKind,
    ChangeSessionStatus,
    FileChangeKind,
    WorkspaceTopologyKind,
)
from code_harness.domain.errors import ChangeSessionNotFoundError, ChangeSessionStoreUnavailableError
from code_harness.domain.models.change_segment import (
    ChangeSessionSegment,
    GitChangeSegment,
    MirrorChangeSegment,
)
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.models.change_set import ChangedFile
from code_harness.domain.models.workspace_manifest import ProposedFileChange
from code_harness.infrastructure.changes.persistence.migrations import (
    apply_change_session_migrations,
)
from code_harness.infrastructure.persistence.connection import connect_database

_TERMINAL = frozenset(
    {
        ChangeSessionStatus.APPLIED,
        ChangeSessionStatus.REJECTED,
        ChangeSessionStatus.CLEANED,
        ChangeSessionStatus.EXPIRED,
    }
)


class SqliteChangeSessionStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def initialize(self) -> None:
        apply_change_session_migrations(self._path)

    def save_session(self, session: ChangeSession) -> None:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                connection.execute(
                    """
                    INSERT INTO change_sessions(
                        session_id, workspace_id, workspace_root, topology_kind, status,
                        created_at, updated_at, expires_at, candidate_digest, approval_id,
                        warnings_json, git_details_json, mirror_details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        workspace_id=excluded.workspace_id,
                        workspace_root=excluded.workspace_root,
                        topology_kind=excluded.topology_kind,
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        expires_at=excluded.expires_at,
                        candidate_digest=excluded.candidate_digest,
                        approval_id=excluded.approval_id,
                        warnings_json=excluded.warnings_json,
                        git_details_json=excluded.git_details_json,
                        mirror_details_json=excluded.mirror_details_json
                    """,
                    (
                        session.session_id,
                        session.workspace_id,
                        session.workspace_root,
                        _topology_value(session.topology_kind),
                        session.status.value,
                        session.created_at,
                        session.updated_at,
                        session.expires_at,
                        session.candidate_digest,
                        session.approval_id,
                        json.dumps(list(session.warnings)),
                        _dump_git_details(session.git_details),
                        _dump_mirror_details(session.mirror_details),
                    ),
                )
                connection.execute(
                    "DELETE FROM change_session_segments WHERE session_id = ?",
                    (session.session_id,),
                )
                for segment in session.segments:
                    connection.execute(
                        """
                        INSERT INTO change_session_segments(
                            session_id, segment_id, kind, relative_root, source_root,
                            isolation_root, status, base_digest, candidate_digest
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session.session_id,
                            segment.segment_id,
                            segment.kind.value,
                            segment.relative_root,
                            segment.source_root,
                            segment.isolation_root,
                            segment.status,
                            segment.base_digest,
                            segment.candidate_digest,
                        ),
                    )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def get_session(self, session_id: str) -> ChangeSession:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                row = connection.execute(
                    "SELECT * FROM change_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise ChangeSessionNotFoundError(session_id)
                segments = connection.execute(
                    """
                    SELECT * FROM change_session_segments
                    WHERE session_id = ?
                    ORDER BY segment_id ASC
                    """,
                    (session_id,),
                ).fetchall()
                return _row_to_session(row, segments)
        except ChangeSessionNotFoundError:
            raise
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def list_sessions(
        self,
        *,
        workspace_id: str | None = None,
        status: ChangeSessionStatus | None = None,
        limit: int = 50,
    ) -> tuple[ChangeSession, ...]:
        self.initialize()
        clauses: list[str] = []
        params: list[object] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        try:
            with connect_database(self._path) as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM change_sessions
                    {where}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
                return tuple(self._hydrate(connection, row) for row in rows)
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def list_non_terminal(self) -> tuple[ChangeSession, ...]:
        self.initialize()
        placeholders = ",".join("?" for _ in _TERMINAL)
        try:
            with connect_database(self._path) as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM change_sessions
                    WHERE status NOT IN ({placeholders})
                    ORDER BY updated_at ASC
                    """,
                    tuple(item.value for item in _TERMINAL),
                ).fetchall()
                return tuple(self._hydrate(connection, row) for row in rows)
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def update_status(
        self,
        session_id: str,
        status: ChangeSessionStatus,
        *,
        updated_at: str,
        candidate_digest: str | None = None,
        approval_id: str | None = None,
        warnings: tuple[str, ...] | None = None,
    ) -> ChangeSession:
        session = self.get_session(session_id)
        updated = ChangeSession(
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            workspace_root=session.workspace_root,
            topology_kind=session.topology_kind,
            status=status,
            created_at=session.created_at,
            updated_at=updated_at,
            expires_at=session.expires_at,
            segments=session.segments,
            candidate_digest=(
                candidate_digest if candidate_digest is not None else session.candidate_digest
            ),
            approval_id=approval_id if approval_id is not None else session.approval_id,
            warnings=warnings if warnings is not None else session.warnings,
            git_details=session.git_details,
            mirror_details=session.mirror_details,
        )
        self.save_session(updated)
        return updated

    def replace_segments(
        self,
        session_id: str,
        segments: tuple[ChangeSessionSegment, ...],
        *,
        git_details: tuple[GitChangeSegment, ...] = (),
        updated_at: str,
    ) -> ChangeSession:
        session = self.get_session(session_id)
        updated = ChangeSession(
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            workspace_root=session.workspace_root,
            topology_kind=session.topology_kind,
            status=session.status,
            created_at=session.created_at,
            updated_at=updated_at,
            expires_at=session.expires_at,
            segments=segments,
            candidate_digest=session.candidate_digest,
            approval_id=session.approval_id,
            warnings=session.warnings,
            git_details=git_details,
            mirror_details=session.mirror_details,
        )
        self.save_session(updated)
        return updated

    def append_event(self, event: ChangeSessionEvent) -> None:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                connection.execute(
                    """
                    INSERT INTO change_session_events(
                        session_id, segment_id, event_type, occurred_at, details_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.session_id,
                        event.segment_id,
                        event.event_type,
                        event.occurred_at,
                        json.dumps(event.details),
                    ),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def list_events(self, session_id: str) -> tuple[ChangeSessionEvent, ...]:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                rows = connection.execute(
                    """
                    SELECT session_id, segment_id, event_type, occurred_at, details_json
                    FROM change_session_events
                    WHERE session_id = ?
                    ORDER BY event_id ASC
                    """,
                    (session_id,),
                ).fetchall()
                return tuple(
                    ChangeSessionEvent(
                        event_type=row["event_type"],
                        occurred_at=row["occurred_at"],
                        session_id=row["session_id"],
                        segment_id=row["segment_id"],
                        details=json.loads(row["details_json"] or "{}"),
                    )
                    for row in rows
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def save_proposed_files(
        self,
        session_id: str,
        segment_id: str,
        changes: tuple[ProposedFileChange, ...],
    ) -> None:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                connection.execute(
                    "DELETE FROM change_session_files WHERE session_id = ? AND segment_id = ?",
                    (session_id, segment_id),
                )
                for change in changes:
                    connection.execute(
                        """
                        INSERT INTO change_session_files(
                            session_id, segment_id, path, operation,
                            base_sha256, proposed_sha256, base_blob_id, proposed_blob_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            segment_id,
                            change.path,
                            change.operation,
                            change.base_sha256,
                            change.proposed_sha256,
                            change.base_blob_id,
                            change.proposed_blob_id,
                        ),
                    )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def list_proposed_files(
        self,
        session_id: str,
        segment_id: str | None = None,
    ) -> tuple[ProposedFileChange, ...]:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                if segment_id is None:
                    rows = connection.execute(
                        """
                        SELECT * FROM change_session_files
                        WHERE session_id = ?
                        ORDER BY segment_id ASC, path ASC
                        """,
                        (session_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM change_session_files
                        WHERE session_id = ? AND segment_id = ?
                        ORDER BY path ASC
                        """,
                        (session_id, segment_id),
                    ).fetchall()
                return tuple(
                    ProposedFileChange(
                        path=row["path"],
                        operation=row["operation"],
                        base_sha256=row["base_sha256"],
                        proposed_sha256=row["proposed_sha256"],
                        base_blob_id=row["base_blob_id"],
                        proposed_blob_id=row["proposed_blob_id"],
                    )
                    for row in rows
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def record_approval(
        self,
        *,
        approval_id: str,
        session_id: str,
        candidate_digest: str,
        state: str,
        created_at: str,
        decided_at: str | None = None,
    ) -> None:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                connection.execute(
                    """
                    INSERT INTO change_session_approvals(
                        approval_id, session_id, candidate_digest, state, created_at, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(approval_id) DO UPDATE SET
                        state=excluded.state,
                        decided_at=excluded.decided_at
                    """,
                    (
                        approval_id,
                        session_id,
                        candidate_digest,
                        state,
                        created_at,
                        decided_at,
                    ),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def record_cleanup_run(
        self,
        *,
        started_at: str,
        finished_at: str | None,
        dry_run: bool,
        details: dict[str, object],
    ) -> None:
        self.initialize()
        try:
            with connect_database(self._path) as connection:
                connection.execute(
                    """
                    INSERT INTO cleanup_runs(started_at, finished_at, dry_run, details_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (started_at, finished_at, 1 if dry_run else 0, json.dumps(details)),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def _hydrate(self, connection: sqlite3.Connection, row: sqlite3.Row) -> ChangeSession:
        segments = connection.execute(
            """
            SELECT * FROM change_session_segments
            WHERE session_id = ?
            ORDER BY segment_id ASC
            """,
            (row["session_id"],),
        ).fetchall()
        return _row_to_session(row, segments)


def _topology_value(value: WorkspaceTopologyKind | str) -> str:
    return value.value if isinstance(value, WorkspaceTopologyKind) else value


def _dump_git_details(details: tuple[GitChangeSegment, ...]) -> str:
    return json.dumps(
        [
            {
                "repository_root": item.repository_root,
                "git_common_dir": item.git_common_dir,
                "target_branch": item.target_branch,
                "base_sha": item.base_sha,
                "temporary_branch": item.temporary_branch,
                "worktree_path": item.worktree_path,
                "candidate_commit": item.candidate_commit,
            }
            for item in details
        ]
    )


def _load_git_details(raw: str) -> tuple[GitChangeSegment, ...]:
    items = json.loads(raw or "[]")
    return tuple(
        GitChangeSegment(
            repository_root=item["repository_root"],
            git_common_dir=item["git_common_dir"],
            target_branch=item["target_branch"],
            base_sha=item["base_sha"],
            temporary_branch=item["temporary_branch"],
            worktree_path=item["worktree_path"],
            candidate_commit=item.get("candidate_commit"),
        )
        for item in items
    )


def _dump_mirror_details(details: tuple[MirrorChangeSegment, ...]) -> str:
    return json.dumps(
        [
            {
                "source_root": item.source_root,
                "mirror_root": item.mirror_root,
                "base_manifest_digest": item.base_manifest_digest,
                "candidate_manifest_digest": item.candidate_manifest_digest,
                "changed_files": [
                    {
                        "path": file.path,
                        "kind": file.kind.value,
                        "old_path": file.old_path,
                        "binary": file.binary,
                        "old_sha256": file.old_sha256,
                        "new_sha256": file.new_sha256,
                    }
                    for file in item.changed_files
                ],
            }
            for item in details
        ]
    )


def _load_mirror_details(raw: str) -> tuple[MirrorChangeSegment, ...]:
    items = json.loads(raw or "[]")
    return tuple(
        MirrorChangeSegment(
            source_root=item["source_root"],
            mirror_root=item["mirror_root"],
            base_manifest_digest=item["base_manifest_digest"],
            candidate_manifest_digest=item.get("candidate_manifest_digest"),
            changed_files=tuple(
                ChangedFile(
                    path=file["path"],
                    kind=FileChangeKind(file["kind"]),
                    old_path=file.get("old_path"),
                    binary=bool(file.get("binary", False)),
                    old_sha256=file.get("old_sha256"),
                    new_sha256=file.get("new_sha256"),
                )
                for file in item.get("changed_files", [])
            ),
        )
        for item in items
    )


def _row_to_session(row: sqlite3.Row, segment_rows: list[sqlite3.Row]) -> ChangeSession:
    return ChangeSession(
        session_id=row["session_id"],
        workspace_id=row["workspace_id"],
        workspace_root=row["workspace_root"],
        topology_kind=WorkspaceTopologyKind(row["topology_kind"]),
        status=ChangeSessionStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        segments=tuple(
            ChangeSessionSegment(
                segment_id=item["segment_id"],
                kind=ChangeSegmentKind(item["kind"]),
                relative_root=item["relative_root"],
                source_root=item["source_root"],
                isolation_root=item["isolation_root"],
                status=item["status"],
                base_digest=item["base_digest"],
                candidate_digest=item["candidate_digest"],
            )
            for item in segment_rows
        ),
        candidate_digest=row["candidate_digest"],
        approval_id=row["approval_id"],
        warnings=tuple(json.loads(row["warnings_json"] or "[]")),
        git_details=_load_git_details(row["git_details_json"]),
        mirror_details=_load_mirror_details(row["mirror_details_json"]),
    )
