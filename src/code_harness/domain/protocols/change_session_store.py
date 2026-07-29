from __future__ import annotations

from typing import Protocol

from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.models.change_segment import ChangeSessionSegment, GitChangeSegment
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.models.workspace_manifest import ProposedFileChange


class ChangeSessionStore(Protocol):
    def initialize(self) -> None: ...

    def save_session(self, session: ChangeSession) -> None: ...

    def get_session(self, session_id: str) -> ChangeSession: ...

    def list_sessions(
        self,
        *,
        workspace_id: str | None = None,
        status: ChangeSessionStatus | None = None,
        limit: int = 50,
    ) -> tuple[ChangeSession, ...]: ...

    def list_non_terminal(self) -> tuple[ChangeSession, ...]: ...

    def update_status(
        self,
        session_id: str,
        status: ChangeSessionStatus,
        *,
        updated_at: str,
        candidate_digest: str | None = None,
        approval_id: str | None = None,
        warnings: tuple[str, ...] | None = None,
    ) -> ChangeSession: ...

    def replace_segments(
        self,
        session_id: str,
        segments: tuple[ChangeSessionSegment, ...],
        *,
        git_details: tuple[GitChangeSegment, ...] = (),
        updated_at: str,
    ) -> ChangeSession: ...

    def append_event(self, event: ChangeSessionEvent) -> None: ...

    def list_events(self, session_id: str) -> tuple[ChangeSessionEvent, ...]: ...

    def save_proposed_files(
        self,
        session_id: str,
        segment_id: str,
        changes: tuple[ProposedFileChange, ...],
    ) -> None: ...

    def list_proposed_files(
        self,
        session_id: str,
        segment_id: str | None = None,
    ) -> tuple[ProposedFileChange, ...]: ...

    def record_approval(
        self,
        *,
        approval_id: str,
        session_id: str,
        candidate_digest: str,
        state: str,
        created_at: str,
        decided_at: str | None = None,
    ) -> None: ...

    def record_cleanup_run(
        self,
        *,
        started_at: str,
        finished_at: str | None,
        dry_run: bool,
        details: dict[str, object],
    ) -> None: ...
