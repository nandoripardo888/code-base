"""Shared fixtures: a small sample project plus isolated runtime state."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from code_harness.paths import PathGuard
from code_harness.session import Session
from code_harness.shell.background import JobRegistry

requires_ripgrep = pytest.mark.skipif(
    shutil.which("rg") is None and shutil.which("rg.exe") is None,
    reason="ripgrep is not installed",
)
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")


@pytest.fixture(autouse=True)
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_HARNESS_HISTORY_DIR", str(tmp_path / "history"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "hello.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )
    (root / "src" / "util.py").write_text("VALUE = 42\nname = 'hello'\n", encoding="utf-8")
    (root / "README.md").write_text("# Sample\n\nhello world\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "notes.txt").write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    return root


@pytest.fixture
def guard(project: Path) -> PathGuard:
    return PathGuard(project)


@pytest.fixture
def jobs(tmp_path: Path) -> Iterator[JobRegistry]:
    registry = JobRegistry(tmp_path / "jobs")
    yield registry
    registry.cleanup()


@pytest.fixture
def session(project: Path) -> Iterator[Session]:
    created = Session.create(project)
    yield created
    created.shutdown()
