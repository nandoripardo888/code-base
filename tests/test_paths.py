from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.errors import (
    InvalidPathKindError,
    PathNotFoundError,
    PathOutsideProjectError,
    ProjectNotFoundError,
)
from code_harness.paths import PathGuard


def test_resolves_relative_path(guard: PathGuard, project: Path) -> None:
    assert guard.resolve("src/hello.py", kind="file") == project / "src" / "hello.py"


def test_resolves_absolute_path_inside_root(guard: PathGuard, project: Path) -> None:
    target = project / "src" / "hello.py"
    assert guard.resolve(str(target), kind="file") == target


def test_rejects_path_outside_root(guard: PathGuard, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(PathOutsideProjectError):
        guard.resolve(str(outside))


def test_rejects_traversal(guard: PathGuard) -> None:
    with pytest.raises(PathOutsideProjectError):
        guard.resolve("../escape.txt")


def test_rejects_empty_path(guard: PathGuard) -> None:
    with pytest.raises(PathOutsideProjectError):
        guard.resolve("   ")


def test_missing_path(guard: PathGuard) -> None:
    with pytest.raises(PathNotFoundError):
        guard.resolve("missing.txt")


def test_wrong_kind(guard: PathGuard) -> None:
    with pytest.raises(InvalidPathKindError):
        guard.resolve("src", kind="file")


def test_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        PathGuard(tmp_path / "nowhere")


def test_additional_root_is_allowed(project: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "job.txt").write_text("output", encoding="utf-8")
    guard = PathGuard(project, additional_roots=(scratch,))
    assert guard.resolve(str(scratch / "job.txt"), kind="file").exists()


def test_relative_falls_back_to_absolute(guard: PathGuard, tmp_path: Path) -> None:
    assert guard.relative(tmp_path / "elsewhere") == (tmp_path / "elsewhere").as_posix()


def test_relative_of_root_is_dot(guard: PathGuard, project: Path) -> None:
    assert guard.relative(project) == "."
