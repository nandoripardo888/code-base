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


def test_path_guard_resolves_directory_within_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "src"
    nested.mkdir(parents=True)

    absolute, relative = PathGuard(root).resolve_within_root(
        "src",
        expected_kind="directory",
        must_exist=True,
    )

    assert Path(absolute) == nested.resolve()
    assert relative == "src"


def test_path_guard_rejects_directory_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()

    with pytest.raises(PathOutsideProjectError):
        PathGuard(root).resolve_within_root("../other", expected_kind="directory")


def test_path_guard_rejects_symlink_directory_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret-dir"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform")

    with pytest.raises(PathOutsideProjectError):
        PathGuard(root).resolve_within_root("escape", expected_kind="directory")


def test_path_guard_rejects_file_when_directory_expected(tmp_path: Path) -> None:
    from code_harness.domain.errors import InvalidPathKindError

    root = tmp_path / "project"
    root.mkdir()
    source = root / "file.txt"
    source.write_text("x", encoding="utf-8")

    with pytest.raises(InvalidPathKindError):
        PathGuard(root).resolve_within_root("file.txt", expected_kind="directory")


def test_path_guard_allows_missing_directory_when_not_required(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    absolute, relative = PathGuard(root).resolve_within_root(
        "future",
        expected_kind="directory",
        must_exist=False,
    )

    assert relative == "future"
    assert Path(absolute) == (root / "future").resolve()


def test_path_guard_strips_extended_prefixes() -> None:
    from code_harness.infrastructure.filesystem.path_guard import _strip_extended_prefix

    assert _strip_extended_prefix(Path(r"\\?\C:\Windows")) == Path(r"C:\Windows")
    assert _strip_extended_prefix(Path(r"\\?\UNC\server\share\path")) == Path(
        r"\\server\share\path"
    )
    assert _strip_extended_prefix(Path(r"C:\plain")) == Path(r"C:\plain")


def test_path_guard_rejects_empty_and_kind_mismatches(tmp_path: Path) -> None:
    from code_harness.domain.errors import InvalidPathKindError, PathOutsideProjectError

    root = tmp_path / "project"
    root.mkdir()
    file_path = root / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    nested = root / "dir"
    nested.mkdir()
    guard = PathGuard(root)

    with pytest.raises(PathOutsideProjectError):
        guard.resolve_within_root("   ", expected_kind="directory")
    with pytest.raises(SourceFileNotFoundError):
        guard.resolve_within_root("missing-any", expected_kind="any", must_exist=True)
    any_abs, any_rel = guard.resolve_within_root("file.txt", expected_kind="any", must_exist=True)
    assert any_rel == "file.txt"
    assert Path(any_abs).is_file()
    with pytest.raises(InvalidPathKindError):
        guard.resolve_within_root("dir", expected_kind="file", must_exist=False)
    with pytest.raises(InvalidPathKindError):
        guard.resolve_within_root("file.txt", expected_kind="directory", must_exist=False)
