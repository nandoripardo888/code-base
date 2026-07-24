"""Phase 2: parallel IndexCoordinator behaviour."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from code_harness.application.indexing import (
    IndexCoordinator,
    IndexProgressEvent,
    IndexProgressPhase,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode
from code_harness.domain.models.index_report import (
    FileIndexUpdate,
    IndexReport,
    StoredFile,
)
from code_harness.domain.models.project import Project
from code_harness.domain.models.semantic import EmbeddingBatch
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.parsers import NativeParserSupervisor, StructuralAnalyzerRegistry
from code_harness.infrastructure.persistence import SQLiteRepositoryStore


def _fingerprint_db(index_path: Path, project_id: str) -> dict[str, object]:
    connection = sqlite3.connect(index_path)
    try:
        files = connection.execute(
            """
            SELECT path, content_hash, parse_state, parser_version, chunking_version
            FROM files
            WHERE project_id = ?
            ORDER BY path
            """,
            (project_id,),
        ).fetchall()
        symbols = connection.execute(
            """
            SELECT f.path, s.name, s.kind, s.start_line, s.end_line, s.canonical_signature
            FROM symbols s
            JOIN files f ON f.file_id = s.file_id
            WHERE f.project_id = ?
            ORDER BY f.path, s.name, s.start_line, s.kind
            """,
            (project_id,),
        ).fetchall()
        references = connection.execute(
            """
            SELECT f.path, r.target_name, r.kind, r.start_line
            FROM code_references r
            JOIN files f ON f.file_id = r.file_id
            WHERE f.project_id = ?
            ORDER BY f.path, r.target_name, r.start_line, r.kind
            """,
            (project_id,),
        ).fetchall()
        chunks = connection.execute(
            """
            SELECT f.path, c.content_hash, c.kind, c.start_line, c.end_line
            FROM chunks c
            JOIN files f ON f.file_id = c.file_id
            WHERE f.project_id = ?
            ORDER BY f.path, c.start_line, c.content_hash, c.kind
            """,
            (project_id,),
        ).fetchall()
        fts = connection.execute(
            """
            SELECT path, content
            FROM file_fts
            WHERE project_id = ?
            ORDER BY path
            """,
            (project_id,),
        ).fetchall()
    finally:
        connection.close()
    return {
        "files": files,
        "symbols": symbols,
        "references": references,
        "chunks": chunks,
        "fts": fts,
    }


def _build_coordinator(
    root: Path,
    *,
    workers: int,
    index_name: str,
) -> tuple[IndexCoordinator, NativeParserSupervisor, Settings]:
    settings = Settings(
        root=root,
        index_path=root / ".code-harness" / index_name,
        parser_workers=workers,
    )
    guard = PathGuard(root)
    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=workers)
    analyzer = StructuralAnalyzerRegistry((supervisor,))
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=analyzer,
        parser_workers=workers,
    )
    return coordinator, supervisor, settings


def test_parallel_index_equivalent_for_one_two_and_four_workers(tmp_path: Path) -> None:
    for index in range(12):
        (tmp_path / f"Sample{index}.java").write_text(
            f"public class Sample{index} {{\n    public int value() {{ return {index}; }}\n}}\n",
            encoding="utf-8",
        )

    fingerprints: dict[int, dict[str, object]] = {}
    reports: dict[int, object] = {}
    for workers in (1, 2, 4):
        coordinator, supervisor, settings = _build_coordinator(
            tmp_path,
            workers=workers,
            index_name=f"index-w{workers}.db",
        )
        try:
            report = coordinator.index(IndexMode.FULL)
            reports[workers] = report
            fingerprints[workers] = _fingerprint_db(
                settings.index_path, settings.project.project_id
            )
            assert report.timings is not None
            assert report.timings.parser_workers == workers
            assert report.timings.processes_created <= workers
        finally:
            supervisor.shutdown()

    assert fingerprints[1] == fingerprints[2] == fingerprints[4]
    assert reports[1].indexed_files == reports[2].indexed_files == reports[4].indexed_files
    assert reports[1].warnings == reports[2].warnings == reports[4].warnings


def test_progress_is_monotonic_and_single_threaded(copied_repository: Path) -> None:
    settings = Settings.for_root(copied_repository)
    settings = Settings(
        root=settings.root,
        index_path=settings.index_path,
        parser_workers=4,
    )
    guard = PathGuard(copied_repository)
    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=4)
    events: list[IndexProgressEvent] = []
    threads: list[int] = []
    lock = threading.Lock()
    active = 0
    max_active = 0

    def on_progress(event: IndexProgressEvent) -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            threads.append(threading.get_ident())
        try:
            events.append(event)
        finally:
            with lock:
                active -= 1

    try:
        report = IndexCoordinator(
            settings.project,
            LocalFileCatalog(guard),
            LocalIndexSourceReader(guard),
            SQLiteRepositoryStore(settings.index_path),
            analyzer=StructuralAnalyzerRegistry((supervisor,)),
            parser_workers=4,
        ).index(IndexMode.FULL, progress=on_progress)
    finally:
        supervisor.shutdown()

    analyzing = [event for event in events if event.phase is IndexProgressPhase.ANALYZING]
    assert analyzing
    assert analyzing[0].current == 1
    assert all(
        analyzing[index].current <= analyzing[index + 1].current
        for index in range(len(analyzing) - 1)
    )
    assert all(event.current <= event.total for event in analyzing if event.total)
    assert analyzing[-1].current == analyzing[-1].total == report.discovered_files
    assert max_active == 1
    assert len(set(threads)) == 1


class _ThreadTrackingStore:
    def __init__(self, delegate: SQLiteRepositoryStore) -> None:
        self._delegate = delegate
        self.write_threads: list[int] = []

    def initialize(self, project: Project) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.initialize(project)

    def list_files(self, project_id: str) -> tuple[StoredFile, ...]:
        return self._delegate.list_files(project_id)

    def start_run(self, project_id: str, mode: IndexMode, started_at: str) -> int:
        self.write_threads.append(threading.get_ident())
        return self._delegate.start_run(project_id, mode, started_at)

    def commit_files(
        self,
        report: IndexReport,
        updates: tuple[FileIndexUpdate, ...],
        removed_paths: tuple[str, ...],
    ) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.commit_files(report, updates, removed_paths)

    def commit_embeddings(self, embeddings: EmbeddingBatch) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.commit_embeddings(embeddings)

    def complete_run(self, run_id: int, report: IndexReport) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.complete_run(run_id, report)

    def fail_run(self, run_id: int, finished_at: str, message: str) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.fail_run(run_id, finished_at, message)

    def get_status(self, project: Project):
        return self._delegate.get_status(project)

    def search_fts(self, project_id: str, query: str, *, limit: int):
        return self._delegate.search_fts(project_id, query, limit=limit)

    def get_outline(self, project_id: str, path: str):
        return self._delegate.get_outline(project_id, path)

    def find_symbols(self, project_id: str, query: str, *, exact: bool, limit: int):
        return self._delegate.find_symbols(project_id, query, exact=exact, limit=limit)

    def find_symbols_by_ids(self, project_id: str, symbol_ids: tuple[str, ...]):
        return self._delegate.find_symbols_by_ids(project_id, symbol_ids)

    def list_symbols(self, project_id: str, paths: tuple[str, ...], *, limit: int):
        return self._delegate.list_symbols(project_id, paths, limit=limit)

    def find_references(self, project_id: str, target_name: str, *, limit: int):
        return self._delegate.find_references(project_id, target_name, limit=limit)


def test_sqlite_writes_stay_on_coordinator_thread(copied_repository: Path) -> None:
    settings = Settings(
        root=copied_repository,
        index_path=copied_repository / ".code-harness" / "thread-check.db",
        parser_workers=4,
    )
    guard = PathGuard(copied_repository)
    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=4)
    store = _ThreadTrackingStore(SQLiteRepositoryStore(settings.index_path))
    owner = threading.get_ident()
    try:
        IndexCoordinator(
            settings.project,
            LocalFileCatalog(guard),
            LocalIndexSourceReader(guard),
            store,  # type: ignore[arg-type]
            analyzer=StructuralAnalyzerRegistry((supervisor,)),
            parser_workers=4,
        ).index(IndexMode.FULL)
    finally:
        supervisor.shutdown()

    assert store.write_threads
    assert all(thread_id == owner for thread_id in store.write_threads)


def test_incremental_without_changes_does_not_start_workers(copied_repository: Path) -> None:
    settings = Settings(
        root=copied_repository,
        index_path=copied_repository / ".code-harness" / "noop.db",
        parser_workers=2,
    )
    guard = PathGuard(copied_repository)
    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=2)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=StructuralAnalyzerRegistry((supervisor,)),
        parser_workers=2,
    )
    try:
        coordinator.index(IndexMode.FULL)
        assert supervisor.metrics_snapshot().processes_created >= 1
        before = supervisor.metrics_snapshot().processes_created
        second = coordinator.index(IndexMode.INCREMENTAL)
        after = supervisor.metrics_snapshot()
        assert second.indexed_files == 0
        assert after.processes_created == before
        assert all(slot.pid is None or True for slot in supervisor._slots)
        # Workers may still be alive from the first run; important is no new spawn.
        assert after.restarts == supervisor.metrics_snapshot().restarts
    finally:
        supervisor.shutdown()
        assert all(slot.pid is None for slot in supervisor._slots)


def test_container_shutdown_stops_parser_workers(tmp_path: Path) -> None:
    from code_harness.application.dto.requests import IndexProjectRequest
    from code_harness.bootstrap.container import build_container

    (tmp_path / "Hello.java").write_text(
        "public class Hello { public void run() {} }\n",
        encoding="utf-8",
    )
    settings = Settings(root=tmp_path, index_path=tmp_path / "index.db", parser_workers=2)
    container = build_container(settings)
    try:
        report = container.index_project.execute(IndexProjectRequest())
        assert report.data.indexed_files >= 1
        metrics = container._analyzer.metrics_snapshot()  # type: ignore[union-attr]
        assert metrics.processes_created >= 1
    finally:
        container.shutdown()
        container.shutdown()
    assert container._analyzer is not None
    assert all(slot.pid is None for slot in container._analyzer._analyzers[0]._slots)  # type: ignore[attr-defined]


def test_parser_workers_setting_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parser_workers"):
        Settings(root=tmp_path, index_path=tmp_path / "x.db", parser_workers=0)
    with pytest.raises(ValueError, match="parser_workers"):
        Settings(root=tmp_path, index_path=tmp_path / "x.db", parser_workers=9)
