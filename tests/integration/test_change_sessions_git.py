from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_harness.bootstrap.changes import build_change_session_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeSessionDigestMismatchError,
    ChangeSessionPathRejectedError,
)
from code_harness.infrastructure.changes.git.git_client import GitClient
from code_harness.infrastructure.changes.git.worktree_manager import assert_under_sessions_home


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init"), cwd=path, check=True, capture_output=True)
    subprocess.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Test"),
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "-b", "main"),
        cwd=path,
        check=False,
        capture_output=True,
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(("git", "add", "README.md"), cwd=path, check=True, capture_output=True)
    subprocess.run(
        ("git", "commit", "-m", "init"),
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def change_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "harness-home"
    home.mkdir()
    monkeypatch.setenv("CODE_HARNESS_HOME", str(home))
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = Settings.for_root(repo)
    container = build_change_session_container(
        settings,
        settings.project,
        recover_on_start=False,
    )
    return repo, settings, container, home


def test_create_worktree_leaves_main_intact(change_env) -> None:
    repo, _settings, container, _home = change_env
    before = (repo / "README.md").read_text(encoding="utf-8")
    session = container.create_session.run()
    assert session.status is ChangeSessionStatus.READY
    assert Path(session.segments[0].isolation_root).is_dir()
    assert (repo / "README.md").read_text(encoding="utf-8") == before
    events = container.inspect_session.events(session.session_id)
    topology_event = next(item for item in events if item.event_type == "topology_resolved")
    worktree_event = next(item for item in events if item.event_type == "worktree_created")
    ready_event = next(item for item in events if item.event_type == "session_ready")
    assert topology_event.details["elapsed_ms"] >= 0
    assert set(worktree_event.details["timings_ms"]) == {
        "snapshot",
        "worktree_add",
        "copy_includes",
        "total",
    }
    assert ready_event.details["persistence_ms"] >= 0
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("agent change\n", encoding="utf-8")
    assert (repo / "README.md").read_text(encoding="utf-8") == before


def test_accept_workspace_patch_and_cleanup(change_env) -> None:
    repo, _settings, container, home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "feature.txt").write_text("new\n", encoding="utf-8")
    prepared, diff = container.prepare_session.run(session.session_id)
    assert prepared.status is ChangeSessionStatus.REVIEW_PENDING
    assert prepared.candidate_digest
    assert "feature.txt" in diff.files
    base = GitClient(repo).rev_parse("HEAD")
    accepted = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest,
    )
    assert accepted.status is ChangeSessionStatus.CLEANED
    head = GitClient(repo).rev_parse("HEAD")
    assert head == base
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "new\n"
    assert not worktree.exists()
    branches = GitClient(repo).run(("branch", "--list", "code-harness/session/*")).stdout
    assert branches.strip() == ""
    session_dir = home / "change-sessions" / session.session_id
    assert not session_dir.exists()


def test_reject_does_not_change_main(change_env) -> None:
    repo, _settings, container, _home = change_env
    base = GitClient(repo).rev_parse("HEAD")
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "nope.txt").write_text("x\n", encoding="utf-8")
    prepared, _diff = container.prepare_session.run(session.session_id)
    rejected = container.reject_session.run(session.session_id)
    assert rejected.status is ChangeSessionStatus.CLEANED
    assert GitClient(repo).rev_parse("HEAD") == base
    assert not (repo / "nope.txt").exists()
    assert prepared.candidate_digest


def test_digest_mismatch_rejects_accept(change_env) -> None:
    _repo, _settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "a.txt").write_text("a\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    with pytest.raises(ChangeSessionDigestMismatchError):
        container.accept_session.run(session.session_id, candidate_digest="deadbeef")
    assert prepared.candidate_digest


def test_dirty_workspace_becomes_isolated_baseline(change_env) -> None:
    repo, _settings, container, _home = change_env
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "README.md").write_text("dirty tracked\n", encoding="utf-8")
    cached_before = GitClient(repo).diff("--cached")
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    assert (worktree / "dirty.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (worktree / "README.md").read_text(encoding="utf-8") == "dirty tracked\n"
    assert GitClient(repo).diff("--cached") == cached_before
    assert session.git_details[0].integration_strategy == "workspace_patch_v2"


def test_conflict_marks_conflict_and_preserves_worktree(change_env) -> None:
    repo, _settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("from agent\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    detail = prepared.git_details[0]
    assert detail.candidate_commit is not None
    (repo / "README.md").write_text("from main\n", encoding="utf-8")
    result = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest or "",
    )
    assert result.status is ChangeSessionStatus.CONFLICT
    assert worktree.exists()
    assert not GitClient(repo).has_ongoing_operation()


def test_conflict_persists_diagnostics_and_retries_same_candidate(change_env) -> None:
    repo, settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("from agent\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    digest = prepared.candidate_digest or ""

    (repo / "README.md").write_text("from main\n", encoding="utf-8")
    conflicted = container.accept_session.run(
        session.session_id,
        candidate_digest=digest,
        approval_id="approval-1",
    )

    assert conflicted.status is ChangeSessionStatus.CONFLICT
    assert conflicted.segments[0].status == ChangeSessionStatus.CONFLICT.value
    assert conflicted.available_actions == ("retry_accept", "reject")
    failure = conflicted.integration_failure
    assert failure is not None
    assert failure.code == "change_session_conflict"
    assert failure.failed_segment == session.segments[0].segment_id
    assert failure.strategy == "workspace_patch_v2"
    assert len(failure.conflicts) == 1
    conflict = failure.conflicts[0]
    assert conflict.path == "README.md"
    assert conflict.kind == "content_conflict"
    assert all(
        value is not None and len(value) == 64
        for value in (
            conflict.base_sha256,
            conflict.current_sha256,
            conflict.proposed_sha256,
        )
    )

    restarted = build_change_session_container(
        settings,
        settings.project,
        recover_on_start=False,
    )
    persisted = restarted.inspect_session.get(session.session_id)
    assert persisted.integration_failure == failure
    failure_event = next(
        item
        for item in restarted.inspect_session.events(session.session_id)
        if item.event_type == "integration_failed"
    )
    assert failure_event.details["failed_segment"] == session.segments[0].segment_id
    assert failure_event.details["conflicts"][0]["path"] == "README.md"
    assert "content" not in failure_event.details["conflicts"][0]

    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    accepted = restarted.accept_session.run(
        session.session_id,
        candidate_digest=digest,
        approval_id="approval-2",
    )
    assert accepted.status is ChangeSessionStatus.CLEANED
    assert accepted.integration_failure is None
    assert accepted.approval_id == "approval-1"
    assert (repo / "README.md").read_text(encoding="utf-8") == "from agent\n"


def test_autocrlf_filtered_bytes_avoid_false_conflict(change_env) -> None:
    repo, _settings, container, _home = change_env
    subprocess.run(
        ("git", "config", "core.autocrlf", "true"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_bytes(b"hello\r\n")
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    assert (worktree / "README.md").read_bytes() == b"hello\r\n"
    (worktree / "README.md").write_bytes(b"agent change\r\n")

    prepared, _ = container.prepare_session.run(session.session_id)
    accepted = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest or "",
    )

    assert accepted.status is ChangeSessionStatus.CLEANED
    assert (repo / "README.md").read_bytes() == b"agent change\r\n"


def test_non_overlapping_merge_add_delete_and_binary(change_env) -> None:
    repo, _settings, container, _home = change_env
    (repo / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "remove.txt").write_text("remove me\n", encoding="utf-8")
    (repo / "asset.bin").write_bytes(b"\x00base\xff")
    subprocess.run(
        ("git", "add", "README.md", "remove.txt", "asset.bin"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "commit", "-m", "add merge fixtures"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("ONE\ntwo\nthree\n", encoding="utf-8")
    (worktree / "remove.txt").unlink()
    (worktree / "asset.bin").write_bytes(b"\x00candidate\xff")
    (worktree / "added.txt").write_text("added\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)

    (repo / "README.md").write_text("one\ntwo\nTHREE\n", encoding="utf-8")
    accepted = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest or "",
    )

    assert accepted.status is ChangeSessionStatus.CLEANED
    assert (repo / "README.md").read_text(encoding="utf-8") == "ONE\ntwo\nTHREE\n"
    assert not (repo / "remove.txt").exists()
    assert (repo / "asset.bin").read_bytes() == b"\x00candidate\xff"
    assert (repo / "added.txt").read_text(encoding="utf-8") == "added\n"


def test_draft_diff_is_side_effect_free_and_includes_all_file_states(change_env) -> None:
    repo, _settings, container, _home = change_env
    (repo / "remove.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ("git", "add", "remove.txt"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "commit", "-m", "add removable file"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("modified\n", encoding="utf-8")
    (worktree / "remove.txt").unlink()
    (worktree / "untracked.txt").write_text("new\n", encoding="utf-8")
    cached_before = GitClient(worktree).diff("--cached", cwd=worktree)
    status_before = GitClient(worktree).status_porcelain(cwd=worktree)
    checkpoints_before = container.patch_engine.list(session.session_id)

    diff = container.inspect_session.get_diff(session.session_id)

    assert diff.state == "draft"
    assert diff.candidate_digest is None
    assert set(diff.files) == {"README.md", "remove.txt", "untracked.txt"}
    assert diff.segments[0].segment_id == session.segments[0].segment_id
    assert "modified" in diff.unified_text
    assert container.inspect_session.get(session.session_id).status is ChangeSessionStatus.READY
    assert GitClient(worktree).diff("--cached", cwd=worktree) == cached_before
    assert GitClient(worktree).status_porcelain(cwd=worktree) == status_before
    assert container.patch_engine.list(session.session_id) == checkpoints_before


def test_prepared_diff_survives_container_restart(change_env) -> None:
    _repo, settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("persisted diff\n", encoding="utf-8")
    prepared, expected = container.prepare_session.run(session.session_id)

    restarted = build_change_session_container(
        settings,
        settings.project,
        recover_on_start=False,
    )
    actual = restarted.inspect_session.get_diff(session.session_id)

    assert actual.state == "prepared"
    assert actual.candidate_digest == prepared.candidate_digest
    assert actual.unified_text == expected.unified_text
    assert actual.files == expected.files
    assert actual.segments == expected.segments


def test_git_common_dir_is_resolved_from_repository_root(
    change_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _settings, _container, _home = change_env
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    assert Path(GitClient(repo).common_dir()) == (repo / ".git").resolve()


def test_codex_patch_checkpoints_support_undo_redo_and_restore(change_env) -> None:
    _repo, _settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    initial = container.patch_engine.list(session.session_id)[0]

    first = container.patch_engine.apply(
        session.session_id,
        """*** Begin Patch
*** Update File: README.md
@@
-hello
+first
*** End Patch""",
        expected_checkpoint_id=initial.checkpoint_id,
    )
    assert (worktree / "README.md").read_text(encoding="utf-8") == "first\n"

    second = container.patch_engine.apply(
        session.session_id,
        """*** Begin Patch
*** Add File: feature.txt
+feature
*** End Patch""",
        expected_checkpoint_id=first.checkpoint_id,
    )
    assert (worktree / "feature.txt").read_text(encoding="utf-8") == "feature\n"

    undone = container.patch_engine.undo(
        session.session_id,
        expected_checkpoint_id=second.checkpoint_id,
    )
    assert undone.checkpoint_id == first.checkpoint_id
    assert not (worktree / "feature.txt").exists()

    redone = container.patch_engine.redo(
        session.session_id,
        expected_checkpoint_id=first.checkpoint_id,
    )
    assert redone.checkpoint_id == second.checkpoint_id
    assert (worktree / "feature.txt").is_file()

    restored = container.patch_engine.restore(
        session.session_id,
        initial.checkpoint_id,
        expected_checkpoint_id=second.checkpoint_id,
    )
    assert restored.checkpoint_id == initial.checkpoint_id
    assert (worktree / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert not (worktree / "feature.txt").exists()


def test_new_patch_after_undo_supersedes_redo(change_env) -> None:
    _repo, _settings, container, _home = change_env
    session = container.create_session.run()
    initial = container.patch_engine.list(session.session_id)[0]
    first = container.patch_engine.apply(
        session.session_id,
        """*** Begin Patch
*** Add File: old.txt
+old
*** End Patch""",
    )
    container.patch_engine.undo(session.session_id)
    replacement = container.patch_engine.apply(
        session.session_id,
        """*** Begin Patch
*** Add File: new.txt
+new
*** End Patch""",
        expected_checkpoint_id=initial.checkpoint_id,
    )
    checkpoints = container.patch_engine.list(session.session_id)
    old_checkpoint = next(item for item in checkpoints if item.checkpoint_id == first.checkpoint_id)
    assert old_checkpoint.state == "superseded"
    assert replacement.checkpoint_id != first.checkpoint_id
    restored = container.patch_engine.restore(
        session.session_id,
        initial.checkpoint_id,
        expected_checkpoint_id=replacement.checkpoint_id,
    )
    assert restored.checkpoint_id == initial.checkpoint_id
    assert not (Path(session.segments[0].isolation_root) / "new.txt").exists()


def test_path_safety_rejects_outside_sessions_home(tmp_path: Path) -> None:
    home = tmp_path / "sessions"
    home.mkdir()
    with pytest.raises(ChangeSessionPathRejectedError):
        assert_under_sessions_home(tmp_path / "other", home)


def test_cleanup_idempotent(change_env) -> None:
    _repo, _settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "x.txt").write_text("x\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    cleaned = container.reject_session.run(session.session_id)
    assert cleaned.status is ChangeSessionStatus.CLEANED
    again = container.cleaner.cleanup_session(
        container.store.get_session(session.session_id),
        preserve_conflict=False,
    )
    assert again.status is ChangeSessionStatus.CLEANED
    assert prepared.candidate_digest
