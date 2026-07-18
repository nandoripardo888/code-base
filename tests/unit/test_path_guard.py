from pathlib import Path

import pytest

from code_harness.domain.errors import (
    PathOutsideProjectError,
    ProjectNotFoundError,
    SourceFileNotFoundError,
)
from code_harness.infrastructure.filesystem.path_guard import PathGuard


def test_path_guard_resolves_relative_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "src" / "file.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")

    resolved, relative = PathGuard(root).resolve_file("src/file.py")

    assert resolved == source.resolve()
    assert relative == "src/file.py"


def test_path_guard_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(PathOutsideProjectError):
        PathGuard(root).resolve_file("../secret.txt")


def test_path_guard_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform")

    with pytest.raises(PathOutsideProjectError):
        PathGuard(root).resolve_file("link.txt")


def test_path_guard_reports_missing_root_and_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        PathGuard(tmp_path / "missing")

    with pytest.raises(SourceFileNotFoundError):
        PathGuard(tmp_path).resolve_file("missing.py")
