import sqlite3
from pathlib import Path

import pytest

from code_harness import CodeHarness
from code_harness.application.indexing import IndexCoordinator
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import DiagnosticStatus, IndexMode, IndexState, MatchType
from code_harness.domain.errors import IndexCorruptedError
from code_harness.domain.models.index_report import IndexedSource
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.persistence import (
    SCHEMA_VERSION,
    SQLiteRepositoryStore,
    apply_migrations,
)
from code_harness.infrastructure.persistence import migrations as migrations_module


class CountingReader:
    def __init__(self, delegate: LocalIndexSourceReader) -> None:
        self.delegate = delegate
        self.paths: list[str] = []

    def load(self, path: str) -> IndexedSource:
        self.paths.append(path)
        return self.delegate.load(path)


def test_indexing_is_incremental_and_search_uses_validated_fts(
    copied_repository: Path,
) -> None:
    (copied_repository / ".pytest_cache").mkdir()
    (copied_repository / ".pytest_cache" / "ignored.py").write_text("ignored = True")
    (copied_repository / ".uv-cache").mkdir()
    (copied_repository / ".uv-cache" / "ignored.py").write_text("ignored = True")
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    reader = CountingReader(LocalIndexSourceReader(guard))
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        reader,
        SQLiteRepositoryStore(settings.index_path),
    )

    first = coordinator.index(IndexMode.INCREMENTAL)
    first_read_count = len(reader.paths)
    second = coordinator.index(IndexMode.INCREMENTAL)

    assert first.state is IndexState.READY
    assert first.indexed_files == first.discovered_files
    assert first_read_count == first.discovered_files
    assert second.indexed_files == 0
    assert second.unchanged_files == first.discovered_files
    assert len(reader.paths) == first_read_count
    assert ".code-harness/index.db" not in reader.paths
    assert ".pytest_cache/ignored.py" not in reader.paths
    assert ".uv-cache/ignored.py" not in reader.paths

    harness = CodeHarness.open(copied_repository)
    result = harness.search_text("AgendaService")

    assert result.index_state == IndexState.READY.value
    assert any(
        hit.match_type.value in {"full_text", "fts_term", "exact_literal", "substring"}
        for hit in result.data
    )
    assert all(len(hit.snippet.file_hash) == 64 for hit in result.data)


def test_index_tracks_changed_removed_and_verify_differences(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)
    first = harness.index_project().data
    source = copied_repository / "src" / "agenda.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    (copied_repository / "README.md").unlink()

    changed = harness.index_project().data

    assert changed.changed_files == 1
    assert changed.removed_files == 1
    assert changed.indexed_files == 1
    assert harness.get_index_status().data.file_count == first.discovered_files - 1

    (copied_repository / "new.py").write_text("new_value = 1\n", encoding="utf-8")
    verified = harness.index_project(IndexMode.VERIFY).data

    assert verified.state is IndexState.READY_WITH_WARNINGS
    assert verified.new_files == 1
    assert harness.get_index_status().data.file_count == first.discovered_files - 1


def test_initialize_status_doctor_and_migrations_are_idempotent(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)

    assert harness.get_index_status().data.state is IndexState.NOT_INITIALIZED
    harness.initialize_index()
    harness.initialize_index()
    status = harness.get_index_status().data
    doctor = harness.doctor().data

    assert status.schema_version == SCHEMA_VERSION
    assert status.state is IndexState.NOT_INITIALIZED
    assert doctor.healthy
    assert all(check.status is DiagnosticStatus.PASS for check in doctor.checks)


def test_corrupt_index_is_reported_and_lexical_search_falls_back(
    copied_repository: Path,
) -> None:
    harness = CodeHarness.open(copied_repository)
    harness.index_project()
    settings = Settings.for_root(copied_repository)
    settings.index_path.write_bytes(b"not a sqlite database")

    with pytest.raises(IndexCorruptedError):
        harness.get_index_status()

    result = harness.search_text("AgendaService")
    doctor = harness.doctor().data

    assert result.data
    assert result.index_state == IndexState.FAILED.value
    assert result.warnings
    assert not doctor.healthy


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "rollback.db"
    monkeypatch.setattr(
        migrations_module,
        "MIGRATIONS",
        ((1, "broken", ("CREATE TABLE temporary(value TEXT)", "INVALID SQL")),),
    )
    monkeypatch.setattr(migrations_module, "SCHEMA_VERSION", 1)

    with pytest.raises(IndexCorruptedError):
        apply_migrations(database)

    with sqlite3.connect(database) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'temporary'"
        ).fetchone()
    assert version == 0
    assert table is None


def test_status_recovers_an_index_run_owned_by_a_dead_process(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    store = SQLiteRepositoryStore(settings.index_path)
    store.initialize(settings.project)
    run_id = store.start_run(
        settings.project.project_id,
        IndexMode.INCREMENTAL,
        "2026-01-01T00:00:00+00:00",
    )
    with sqlite3.connect(settings.index_path) as connection:
        connection.execute(
            "UPDATE index_runs SET owner_pid = ? WHERE run_id = ?",
            (2_000_000_000, run_id),
        )

    status = store.get_status(settings.project)

    assert status.state is IndexState.FAILED
    assert status.last_run is not None
    assert status.last_run.state is IndexState.FAILED
    assert any("interrupted index run" in warning for warning in status.warnings)
