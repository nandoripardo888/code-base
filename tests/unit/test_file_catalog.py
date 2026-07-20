import shutil
import subprocess
from pathlib import Path

import pytest

from code_harness.infrastructure.filesystem.local_file_catalog import LocalFileCatalog
from code_harness.infrastructure.filesystem.path_guard import PathGuard


def test_catalog_applies_safe_ignores_and_gitignore(copied_repository: Path) -> None:
    catalog = LocalFileCatalog(PathGuard(copied_repository))

    paths = {item.path for item in catalog.list_files()}

    assert "src/AgendaService.java" in paths
    assert "src/agenda.py" in paths
    assert "ignored.sql" not in paths
    assert "target/generated.java" not in paths


def test_catalog_applies_include_and_exclude_globs(copied_repository: Path) -> None:
    catalog = LocalFileCatalog(PathGuard(copied_repository))

    files = catalog.list_files(include_globs=("*.java", "*.py"), exclude_globs=("*.py",))

    assert [item.path for item in files] == ["src/AgendaService.java"]
    assert files[0].language == "java"


def test_catalog_uses_git_to_apply_nested_gitignores(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable")
    root = tmp_path / "repository"
    generated = root / "module" / "generated"
    generated.mkdir(parents=True)
    tracked = generated / "tracked.py"
    tracked.write_text("tracked = True\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "init"), check=True, capture_output=True)
    subprocess.run(
        ("git", "-C", str(root), "add", "module/generated/tracked.py"),
        check=True,
        capture_output=True,
    )
    (root / "module" / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (generated / "ignored.py").write_text("ignored = True\n", encoding="utf-8")

    paths = {item.path for item in LocalFileCatalog(PathGuard(root)).list_files()}

    assert "module/generated/tracked.py" in paths
    assert "module/generated/ignored.py" not in paths
    assert "module/.gitignore" in paths


def test_catalog_applies_nested_gitignores_without_a_git_repository(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    generated = root / "module" / "generated"
    generated.mkdir(parents=True)
    (root / "module" / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (generated / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    source = root / "module" / "source.py"
    source.write_text("source = True\n", encoding="utf-8")

    paths = {item.path for item in LocalFileCatalog(PathGuard(root)).list_files()}

    assert "module/source.py" in paths
    assert "module/generated/ignored.py" not in paths


def test_catalog_does_not_follow_file_symlink_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass", encoding="utf-8")
    try:
        (root / "outside.py").symlink_to(outside)
    except OSError:
        return

    assert LocalFileCatalog(PathGuard(root)).list_files() == ()
