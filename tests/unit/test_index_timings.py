from datetime import UTC, datetime
from pathlib import Path

from code_harness.application.indexing import IndexCoordinator
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.persistence import SQLiteRepositoryStore


def test_index_report_includes_structural_timings(copied_repository: Path) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
    )

    report = coordinator.index(IndexMode.INCREMENTAL)

    assert report.timings is not None
    assert report.timings.total_ms >= 0
    assert report.timings.initialize_ms >= 0
    assert report.timings.discovery_ms >= 0
    assert report.timings.read_hash_ms >= 0
    assert report.timings.chunk_build_ms >= 0
    assert report.timings.commit_ms >= 0
    assert report.timings.commit_metadata_ms >= 0
    assert report.timings.commit_fts_ms >= 0
    assert report.timings.commit_structure_ms >= 0
    assert report.timings.commit_embeddings_ms >= 0
    assert report.timings.commit_finalize_ms >= 0
    assert report.timings.analyzed_files == report.indexed_files
    assert report.timings.avg_file_bytes > 0


def test_second_incremental_run_keeps_timings_without_reanalysis(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
    )
    coordinator.index(IndexMode.INCREMENTAL)

    second = coordinator.index(IndexMode.INCREMENTAL)

    assert second.timings is not None
    assert second.indexed_files == 0
    assert second.timings.analyzed_files == 0
    assert second.timings.processes_created == 0


def test_finished_at_and_persisted_duration_include_payload_commit(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    instants = iter(
        (
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
    )
    store = SQLiteRepositoryStore(settings.index_path)
    report = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        store,
        clock=lambda: next(instants),
    ).index(IndexMode.INCREMENTAL)
    status = store.get_status(settings.project)

    assert report.started_at == "2026-01-01T00:00:00+00:00"
    assert report.finished_at == "2026-01-01T00:00:10+00:00"
    assert status.last_run is not None
    assert status.last_run.duration_ms == 10_000
