from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.bootstrap.changes import build_change_session_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ChangeSessionStatus, WorkspaceTopologyKind


@pytest.fixture
def mirror_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "harness-home"
    home.mkdir()
    monkeypatch.setenv("CODE_HARNESS_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (workspace / "README.md").write_text("docs\n", encoding="utf-8")
    settings = Settings.for_root(workspace)
    container = build_change_session_container(
        settings,
        settings.project,
        recover_on_start=False,
    )
    return workspace, settings, container, home


def test_mirror_create_and_accept(mirror_env) -> None:
    workspace, _settings, container, _home = mirror_env
    session = container.create_session.run()
    assert session.topology_kind is WorkspaceTopologyKind.NON_GIT
    assert session.status is ChangeSessionStatus.READY
    mirror = Path(session.segments[0].isolation_root)
    assert mirror.is_dir()
    (mirror / "src" / "app.py").write_text("print('agent')\n", encoding="utf-8")
    (mirror / "new.txt").write_text("added\n", encoding="utf-8")
    original = (workspace / "src" / "app.py").read_text(encoding="utf-8")
    assert original == "print('hi')\n"
    prepared, diff = container.prepare_session.run(session.session_id)
    assert prepared.status is ChangeSessionStatus.REVIEW_PENDING
    assert prepared.candidate_digest
    assert (
        "src/app.py" in diff.files
        or "src\\app.py" in diff.files
        or any(item.endswith("app.py") for item in diff.files)
    )
    accepted = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest,
    )
    assert accepted.status is ChangeSessionStatus.CLEANED
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('agent')\n"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "added\n"


def test_mirror_reject_leaves_original(mirror_env) -> None:
    workspace, _settings, container, _home = mirror_env
    session = container.create_session.run()
    mirror = Path(session.segments[0].isolation_root)
    (mirror / "README.md").write_text("changed\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    rejected = container.reject_session.run(session.session_id)
    assert rejected.status is ChangeSessionStatus.CLEANED
    assert (workspace / "README.md").read_text(encoding="utf-8") == "docs\n"
    assert prepared.candidate_digest


def test_mirror_stale_when_original_changes(mirror_env) -> None:
    workspace, _settings, container, _home = mirror_env
    session = container.create_session.run()
    mirror = Path(session.segments[0].isolation_root)
    (mirror / "README.md").write_text("from agent\n", encoding="utf-8")
    prepared, _ = container.prepare_session.run(session.session_id)
    (workspace / "README.md").write_text("from user\n", encoding="utf-8")
    result = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest or "",
    )
    assert result.status is ChangeSessionStatus.STALE
    assert (workspace / "README.md").read_text(encoding="utf-8") == "from user\n"


def test_mirror_draft_diff_does_not_prepare_or_persist_candidate(mirror_env) -> None:
    _workspace, _settings, container, _home = mirror_env
    session = container.create_session.run()
    mirror = Path(session.segments[0].isolation_root)
    (mirror / "src" / "app.py").write_text("print('draft')\n", encoding="utf-8")
    (mirror / "README.md").unlink()
    (mirror / "new.txt").write_text("new\n", encoding="utf-8")

    diff = container.inspect_session.get_diff(session.session_id)

    assert diff.state == "draft"
    assert diff.candidate_digest is None
    assert set(diff.files) == {"README.md", "new.txt", "src/app.py"}
    assert diff.segments[0].segment_id == session.segments[0].segment_id
    persisted = container.inspect_session.get(session.session_id)
    assert persisted.status is ChangeSessionStatus.READY
    assert persisted.candidate_digest is None
    assert (
        container.store.list_proposed_files(
            session.session_id,
            session.segments[0].segment_id,
        )
        == ()
    )
