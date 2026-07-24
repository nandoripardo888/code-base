from pathlib import Path

from code_harness.application.indexing import (
    IndexCoordinator,
    IndexProgressEvent,
    IndexProgressPhase,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.interfaces.cli.renderers import progress as progress_module
from code_harness.interfaces.cli.renderers.progress import IndexProgressPrinter


def test_index_coordinator_emits_analyze_progress(copied_repository: Path) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    events: list[IndexProgressEvent] = []

    report = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
    ).index(IndexMode.FULL, progress=events.append)

    phases = [event.phase for event in events]
    assert IndexProgressPhase.DISCOVERING in phases
    assert IndexProgressPhase.ANALYZING in phases
    assert IndexProgressPhase.COMMITTING in phases
    assert IndexProgressPhase.COMPLETE in phases

    analyzing = [event for event in events if event.phase is IndexProgressPhase.ANALYZING]
    assert analyzing
    assert analyzing[0].current == 1
    assert analyzing[-1].current == analyzing[-1].total == report.discovered_files
    assert analyzing[-1].percent == 100
    assert all(event.path for event in analyzing)


def test_index_progress_printer_reports_percent_without_tty() -> None:
    lines: list[str] = []
    printer = IndexProgressPrinter(stream_is_tty=False)
    original = progress_module._echo_err
    progress_module._echo_err = lines.append
    try:
        printer(
            IndexProgressEvent(
                IndexProgressPhase.DISCOVERING,
                message="Discovering project files",
            )
        )
        printer(IndexProgressEvent(IndexProgressPhase.ANALYZING, current=1, total=20, path="a.py"))
        printer(IndexProgressEvent(IndexProgressPhase.ANALYZING, current=5, total=20, path="b.py"))
        printer(IndexProgressEvent(IndexProgressPhase.ANALYZING, current=20, total=20, path="z.py"))
        printer(IndexProgressEvent(IndexProgressPhase.COMMITTING, message="Writing index to disk"))
        printer(IndexProgressEvent(IndexProgressPhase.COMPLETE, current=20, total=20))
    finally:
        progress_module._echo_err = original

    assert lines[0] == "Indexing: discovering files..."
    assert any("5/20 (25%)" in line for line in lines)
    assert any("20/20 (100%)" in line for line in lines)
    assert "Indexing: writing index to disk..." in lines
    assert "Indexing: complete." in lines
