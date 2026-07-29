from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus, WorkspaceTopologyKind
from code_harness.domain.models.change_segment import ChangeSessionSegment, GitChangeSegment
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.infrastructure.changes.persistence.content_addressed_blob_store import (
    ContentAddressedBlobStore,
)
from code_harness.infrastructure.changes.persistence.sqlite_change_session_store import (
    SqliteChangeSessionStore,
)


def test_change_session_store_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "change-sessions.db"
    store = SqliteChangeSessionStore(db)
    store.initialize()
    now = datetime.now(UTC).isoformat()
    session = ChangeSession(
        session_id="sess1",
        workspace_id="ws1",
        workspace_root=str(tmp_path / "repo"),
        topology_kind=WorkspaceTopologyKind.SINGLE_GIT,
        status=ChangeSessionStatus.READY,
        created_at=now,
        updated_at=now,
        expires_at=None,
        segments=(
            ChangeSessionSegment(
                segment_id="root",
                kind=ChangeSegmentKind.GIT_WORKTREE,
                relative_root=".",
                source_root=str(tmp_path / "repo"),
                isolation_root=str(tmp_path / "wt"),
                status="ready",
                base_digest="abc",
            ),
        ),
        candidate_digest=None,
        approval_id=None,
        git_details=(
            GitChangeSegment(
                repository_root=str(tmp_path / "repo"),
                git_common_dir=str(tmp_path / "repo" / ".git"),
                target_branch="main",
                base_sha="abc",
                temporary_branch="code-harness/session/sess1/root",
                worktree_path=str(tmp_path / "wt"),
            ),
        ),
    )
    store.save_session(session)
    store.append_event(
        ChangeSessionEvent(
            event_type="session_created",
            occurred_at=now,
            session_id="sess1",
        )
    )
    loaded = store.get_session("sess1")
    assert loaded.session_id == "sess1"
    assert loaded.status is ChangeSessionStatus.READY
    assert loaded.segments[0].segment_id == "root"
    assert loaded.git_details[0].temporary_branch.endswith("/root")
    events = store.list_events("sess1")
    assert len(events) == 1
    assert events[0].event_type == "session_created"


def test_blob_store_put_refcount_and_gc(tmp_path: Path) -> None:
    db = tmp_path / "change-sessions.db"
    blobs = tmp_path / "blobs"
    store = ContentAddressedBlobStore(db, blobs)
    blob_id = store.put(b"hello world", ref_owner="sess1", ref_kind="base")
    assert store.get(blob_id) == b"hello world"
    store.add_reference(blob_id, ref_owner="sess1", ref_kind="proposed")
    store.release_reference(blob_id, ref_owner="sess1", ref_kind="base")
    assert store.gc() == 0
    store.release_owner("sess1")
    assert store.gc() == 1
    assert not (blobs / blob_id[:2] / blob_id).exists()
