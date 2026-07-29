from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from code_harness.bootstrap.changes import build_change_session_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeSessionDigestMismatchError,
    ChangeSessionPathRejectedError,
    ChangeSessionWorkspaceDirtyError,
)
from code_harness.domain.models.change_segment import GitChangeSegment
from code_harness.domain.models.change_session import ChangeSession
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
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("agent change\n", encoding="utf-8")
    assert (repo / "README.md").read_text(encoding="utf-8") == before


def test_accept_cherry_pick_and_cleanup(change_env) -> None:
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
    assert head != base
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


def test_dirty_workspace_blocks_create(change_env) -> None:
    repo, _settings, container, _home = change_env
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ChangeSessionWorkspaceDirtyError):
        container.create_session.run()


def test_conflict_marks_conflict_and_preserves_worktree(change_env) -> None:
    repo, _settings, container, _home = change_env
    session = container.create_session.run()
    worktree = Path(session.segments[0].isolation_root)
    (worktree / "README.md").write_text("from agent\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    detail = prepared.git_details[0]
    assert detail.candidate_commit is not None
    (repo / "README.md").write_text("from main\n", encoding="utf-8")
    subprocess.run(("git", "add", "README.md"), cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ("git", "commit", "-m", "main side"),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    current = GitClient(repo).rev_parse("HEAD")
    updated_detail = GitChangeSegment(
        repository_root=detail.repository_root,
        git_common_dir=detail.git_common_dir,
        target_branch=detail.target_branch,
        base_sha=current,
        temporary_branch=detail.temporary_branch,
        worktree_path=detail.worktree_path,
        candidate_commit=detail.candidate_commit,
    )
    refreshed = container.store.get_session(session.session_id)
    container.store.save_session(
        ChangeSession(
            session_id=refreshed.session_id,
            workspace_id=refreshed.workspace_id,
            workspace_root=refreshed.workspace_root,
            topology_kind=refreshed.topology_kind,
            status=refreshed.status,
            created_at=refreshed.created_at,
            updated_at=datetime.now(UTC).isoformat(),
            expires_at=refreshed.expires_at,
            segments=refreshed.segments,
            candidate_digest=refreshed.candidate_digest,
            approval_id=refreshed.approval_id,
            warnings=refreshed.warnings,
            git_details=(updated_detail,),
            mirror_details=refreshed.mirror_details,
        )
    )
    result = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest or "",
    )
    assert result.status is ChangeSessionStatus.CONFLICT
    assert worktree.exists()
    assert not GitClient(repo).has_ongoing_operation()


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
