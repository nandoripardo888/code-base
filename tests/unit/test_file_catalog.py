from pathlib import Path

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
