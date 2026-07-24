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
    assert report.timings.discovery_ms >= 0
    assert report.timings.read_hash_ms >= 0
    assert report.timings.chunk_build_ms >= 0
    assert report.timings.commit_ms >= 0
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
