from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_harness.bootstrap.changes import build_change_session_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ChangeSessionStatus, WorkspaceTopologyKind


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
    subprocess.run(("git", "checkout", "-b", "main"), cwd=path, check=False, capture_output=True)
    (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
    subprocess.run(("git", "add", "README.md"), cwd=path, check=True, capture_output=True)
    subprocess.run(("git", "commit", "-m", "init"), cwd=path, check=True, capture_output=True)


def test_composite_create_prepare_accept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "harness-home"
    home.mkdir()
    monkeypatch.setenv("CODE_HARNESS_HOME", str(home))
    root = tmp_path / "workspace"
    root.mkdir()
    _init_repo(root / "crmservice")
    _init_repo(root / "framework")
    docs = root / "documentos"
    docs.mkdir()
    (docs / "notes.txt").write_text("notes\n", encoding="utf-8")
    settings = Settings.for_root(root)
    container = build_change_session_container(
        settings,
        settings.project,
        recover_on_start=False,
    )
    session = container.create_session.run()
    assert session.topology_kind is WorkspaceTopologyKind.COMPOSITE
    assert len(session.segments) >= 3
    assert session.warnings
    # Edit one git segment and the loose mirror.
    crm = next(s for s in session.segments if s.segment_id == "crmservice")
    loose = next(s for s in session.segments if s.segment_id == "loose-files")
    (Path(crm.isolation_root) / "feature.txt").write_text("crm\n", encoding="utf-8")
    (Path(loose.isolation_root) / "documentos" / "notes.txt").write_text(
        "updated\n",
        encoding="utf-8",
    )
    prepared, diff = container.prepare_session.run(session.session_id)
    assert prepared.status is ChangeSessionStatus.REVIEW_PENDING
    assert prepared.candidate_digest
    accepted = container.accept_session.run(
        session.session_id,
        candidate_digest=prepared.candidate_digest,
    )
    assert accepted.status is ChangeSessionStatus.CLEANED
    assert (root / "crmservice" / "feature.txt").read_text(encoding="utf-8") == "crm\n"
    assert (root / "documentos" / "notes.txt").read_text(encoding="utf-8") == "updated\n"
    assert diff.files
