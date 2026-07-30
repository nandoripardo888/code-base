from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from code_harness.errors import PatchHistoryError
from code_harness.history import (
    FileSnapshot,
    HistoryManager,
    HistoryPolicy,
    default_history_root,
)


def _snapshot(
    manager: HistoryManager,
    path: str,
    before: bytes | None,
    after: bytes | None,
    operation: str = "modify",
) -> FileSnapshot:
    return FileSnapshot(
        path=path,
        operation=operation,
        existed_before=before is not None,
        exists_after=after is not None,
        before_sha256=None if before is None else __import__("hashlib").sha256(before).hexdigest(),
        after_sha256=None if after is None else __import__("hashlib").sha256(after).hexdigest(),
        before_object=None if before is None else manager.store_object(before),
        after_object=None if after is None else manager.store_object(after),
        encoding="utf-8",
        line_ending="LF",
    )


def _transaction(
    manager: HistoryManager,
    snapshot: FileSnapshot,
    *,
    status: str,
    patch: str = "patch",
):
    manifest = manager.begin(patch, (snapshot,), git_version="git version test")
    return manager.update(manifest, status=status)


def test_default_history_root_honors_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "custom-history"
    monkeypatch.setenv("CODE_HARNESS_HISTORY_DIR", str(target))
    assert default_history_root() == target.resolve()


def test_history_policy_reads_and_sanitizes_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_HARNESS_HISTORY_RETENTION_DAYS", "invalid")
    monkeypatch.setenv("CODE_HARNESS_HISTORY_KEEP_LAST", "0")
    monkeypatch.setenv("CODE_HARNESS_HISTORY_MAX_WORKSPACE_MB", "2")
    policy = HistoryPolicy.from_environment()
    assert policy.retention_days == 30
    assert policy.keep_last_per_workspace == 1
    assert policy.max_workspace_bytes == 2 * 1024 * 1024


def test_history_store_object_is_content_addressed(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    first = manager.store_object(b"same")
    second = manager.store_object(b"same")
    assert first == second
    assert manager.read_object(first) == b"same"


def test_history_rejects_invalid_or_missing_identifiers(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    with pytest.raises(PatchHistoryError, match="Invalid history object"):
        manager.read_object("invalid")
    with pytest.raises(PatchHistoryError, match="Invalid transaction"):
        manager.load("../escape")
    with pytest.raises(PatchHistoryError, match="not found"):
        manager.load("missing")


def test_maintain_recovers_completed_applying_transaction(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    target = project / "state.txt"
    before = b"before\n"
    after = b"after\n"
    target.write_bytes(after)
    manifest = _transaction(
        manager, _snapshot(manager, "state.txt", before, after), status="applying"
    )

    result = manager.maintain()

    assert result["recovered"] == 1
    assert manager.load(manifest.transaction_id).status == "applied"
    assert target.read_bytes() == after


def test_maintain_restores_interrupted_partial_commit(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    first = project / "first.txt"
    second = project / "second.txt"
    first_before, first_after = b"one\n", b"ONE\n"
    second_before, second_after = b"two\n", b"TWO\n"
    first.write_bytes(first_after)
    second.write_bytes(b"unexpected\n")
    snapshots = (
        _snapshot(manager, "first.txt", first_before, first_after),
        _snapshot(manager, "second.txt", second_before, second_after),
    )
    manifest = manager.begin("patch", snapshots, git_version="git version test")
    manifest = manager.update(manifest, status="applying")

    manager.maintain()

    recovered = manager.load(manifest.transaction_id)
    assert recovered.status == "failed_and_restored"
    assert first.read_bytes() == first_before
    assert second.read_bytes() == second_before


def test_maintain_marks_prepared_transaction_not_applied(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    snapshot = _snapshot(manager, "src/hello.py", b"before", b"after")
    manifest = manager.begin("patch", (snapshot,), git_version="git version test")

    manager.maintain()

    assert manager.load(manifest.transaction_id).status == "not_applied"


def test_cleanup_keeps_latest_and_removes_old_transactions(project: Path, tmp_path: Path) -> None:
    policy = HistoryPolicy(
        retention_days=1,
        keep_last_per_workspace=1,
        max_workspace_bytes=100_000_000,
        max_global_bytes=100_000_000,
        stale_temp_hours=24,
    )
    manager = HistoryManager(project, history_root=tmp_path / "history", policy=policy)
    old_snapshot = _snapshot(manager, "old.txt", b"old-before", b"old-after")
    old = _transaction(manager, old_snapshot, status="applied", patch="old patch")
    old_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    old = replace(old, created_at=old_date, updated_at=old_date)
    manager.save(old)
    latest = _transaction(
        manager,
        _snapshot(manager, "latest.txt", b"latest-before", b"latest-after"),
        status="applied",
        patch="latest patch",
    )

    result = manager.maintain()

    assert result["removed_transactions"] == 1
    assert not (manager.transactions_dir / old.transaction_id).exists()
    assert (manager.transactions_dir / latest.transaction_id).exists()
    assert old_snapshot.before_object is not None
    assert not (
        manager.objects_dir / old_snapshot.before_object[:2] / old_snapshot.before_object
    ).exists()


def test_cleanup_removes_stale_temporary_workspace(project: Path, tmp_path: Path) -> None:
    policy = HistoryPolicy(stale_temp_hours=1)
    manager = HistoryManager(project, history_root=tmp_path / "history", policy=policy)
    stale = manager.make_temporary_workspace("stale")
    old = datetime.now().timestamp() - 7200
    os.utime(stale, (old, old))

    result = manager.maintain()

    assert result["removed_temporary"] == 1
    assert not stale.exists()


def test_force_rollback_overwrites_later_changes(project: Path, tmp_path: Path) -> None:
    manager = HistoryManager(project, history_root=tmp_path / "history")
    target = project / "force.txt"
    before, after = b"before\n", b"after\n"
    target.write_bytes(after)
    manifest = _transaction(
        manager, _snapshot(manager, "force.txt", before, after), status="applied"
    )
    target.write_bytes(b"later\n")

    result = manager.rollback(manifest.transaction_id, force=True)

    assert result["forced"] is True
    assert target.read_bytes() == before
    assert manager.load(manifest.transaction_id).status == "rolled_back"
