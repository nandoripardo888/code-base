from __future__ import annotations

import subprocess
from pathlib import Path

from code_harness.domain.enums import ChangeSegmentKind, WorkspaceTopologyKind
from code_harness.infrastructure.changes.topology.filesystem_topology_resolver import (
    FilesystemTopologyResolver,
)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ("git", "init"),
        cwd=path,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=path,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ("git", "config", "user.name", "Test"),
        cwd=path,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ("git", "add", "README.md"),
        cwd=path,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ("git", "commit", "-m", "init"),
        cwd=path,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def test_topology_single_git(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _init_repo(root)
    topology = FilesystemTopologyResolver().resolve(str(root))
    assert topology.kind is WorkspaceTopologyKind.SINGLE_GIT
    assert len(topology.git_repositories) == 1
    assert topology.loose_roots == ()
    assert len(topology.segments) == 1
    assert topology.segments[0].kind is ChangeSegmentKind.GIT_WORKTREE
    assert topology.segments[0].segment_id == "root"


def test_topology_non_git(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    topology = FilesystemTopologyResolver().resolve(str(root))
    assert topology.kind is WorkspaceTopologyKind.NON_GIT
    assert topology.git_repositories == ()
    assert len(topology.segments) == 1
    assert topology.segments[0].kind is ChangeSegmentKind.WORKSPACE_MIRROR


def test_topology_composite_multi_repo(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init_repo(root / "crmservice")
    _init_repo(root / "framework")
    docs = root / "documentos"
    docs.mkdir()
    (docs / "notes.txt").write_text("n\n", encoding="utf-8")
    topology = FilesystemTopologyResolver().resolve(str(root))
    assert topology.kind is WorkspaceTopologyKind.COMPOSITE
    assert len(topology.git_repositories) == 2
    kinds = {segment.kind for segment in topology.segments}
    assert ChangeSegmentKind.GIT_WORKTREE in kinds
    assert ChangeSegmentKind.WORKSPACE_MIRROR in kinds
    git_ids = {
        segment.segment_id
        for segment in topology.segments
        if segment.kind is ChangeSegmentKind.GIT_WORKTREE
    }
    assert "crmservice" in git_ids
    assert "framework" in git_ids


def test_topology_nested_gitfile(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    nested = root / "lib"
    nested.mkdir()
    # Simulate a worktree/.git file pointing at a fake gitdir.
    gitdir = tmp_path / "external-gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "objects").mkdir()
    (nested / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    topology = FilesystemTopologyResolver().resolve(str(root))
    assert len(topology.git_repositories) == 1
    assert Path(topology.git_repositories[0]).resolve() == nested.resolve()
