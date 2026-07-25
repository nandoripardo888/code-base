"""Phase 3: partial indexing by path/glob."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from code_harness import CodeHarness
from code_harness.application.indexing import IndexCoordinator, IndexScope
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode, IndexState
from code_harness.domain.models.index_report import (
    CommitFilesMetrics,
    IndexReport,
    PersistenceProgress,
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


def _write_fixture(root: Path) -> None:
    (root / "src").mkdir()
    (root / "docs").mkdir()
    (root / "generated").mkdir()
    (root / "src" / "A.java").write_text(
        "public class A { public int value() { return 1; } }\n",
        encoding="utf-8",
    )
    (root / "src" / "B.java").write_text(
        "public class B { public int value() { return 2; } }\n",
        encoding="utf-8",
    )
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "generated" / "C.java").write_text(
        "public class C { public int value() { return 3; } }\n",
        encoding="utf-8",
    )


def _indexed_paths(index_path: Path, project_id: str) -> set[str]:
    connection = sqlite3.connect(index_path)
    try:
        rows = connection.execute(
            "SELECT path FROM files WHERE project_id = ? ORDER BY path",
            (project_id,),
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}


def _build_coordinator(
    root: Path,
    *,
    workers: int = 1,
    index_name: str = "index.db",
) -> tuple[IndexCoordinator, NativeParserSupervisor, Settings]:
    settings = Settings(
        root=root,
        index_path=root / ".code-harness" / index_name,
        parser_workers=workers,
    )
    guard = PathGuard(root)
    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=workers)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=StructuralAnalyzerRegistry((supervisor,)),
        parser_workers=workers,
    )
    return coordinator, supervisor, settings


def test_index_scope_matches_include_and_exclude() -> None:
    scope = IndexScope(include_globs=("src",), exclude_globs=("src/B.java",))

    assert scope.partial
    assert scope.matches("src/A.java")
    assert not scope.matches("src/B.java")
    assert not scope.matches("docs/guide.md")
    assert IndexScope().matches("docs/guide.md")
    assert not IndexScope().partial


def test_full_index_registers_all_allowed_files(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        report = coordinator.index(IndexMode.FULL)
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert paths == {"docs/guide.md", "generated/C.java", "src/A.java", "src/B.java"}
    assert report.partial is False
    assert report.preserved_out_of_scope_files == 0
    assert report.discovered_files == 4


@pytest.mark.parametrize("workers", (1, 4))
def test_partial_include_removes_only_in_scope_missing_file(tmp_path: Path, workers: int) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(
        tmp_path,
        workers=workers,
        index_name=f"partial-include-w{workers}.db",
    )
    try:
        coordinator.index(IndexMode.FULL)
        (tmp_path / "src" / "A.java").unlink()
        (tmp_path / "docs" / "guide.md").unlink()
        report = coordinator.index(IndexMode.INCREMENTAL, include_globs=("src",))
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert "src/A.java" not in paths
    assert "src/B.java" in paths
    assert "docs/guide.md" in paths
    assert "generated/C.java" in paths
    assert report.partial is True
    assert report.include_globs == ("src",)
    assert report.removed_files == 1
    assert report.preserved_out_of_scope_files == 2
    assert report.scoped_discovered_files == 1


def test_partial_exclude_preserves_excluded_entry(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        coordinator.index(IndexMode.FULL)
        (tmp_path / "src" / "B.java").unlink()
        report = coordinator.index(
            IndexMode.INCREMENTAL,
            include_globs=("src",),
            exclude_globs=("src/B.java",),
        )
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert "src/B.java" in paths
    assert "src/A.java" in paths
    assert report.partial is True
    assert report.exclude_globs == ("src/B.java",)
    assert report.removed_files == 0
    assert report.preserved_out_of_scope_files >= 1


def test_partial_exclude_only_filters_discovery(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        full = coordinator.index(IndexMode.FULL)
        report = coordinator.index(IndexMode.INCREMENTAL, exclude_globs=("generated",))
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert "generated/C.java" in paths
    assert report.partial is True
    assert report.discovered_files == full.discovered_files - 1
    assert report.preserved_out_of_scope_files == 1
    assert report.indexed_files == 0


def test_without_filters_removes_missing_files(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        coordinator.index(IndexMode.FULL)
        (tmp_path / "src" / "A.java").unlink()
        (tmp_path / "docs" / "guide.md").unlink()
        report = coordinator.index(IndexMode.INCREMENTAL)
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert paths == {"generated/C.java", "src/B.java"}
    assert report.partial is False
    assert report.removed_files == 2
    assert report.preserved_out_of_scope_files == 0


def test_verify_partial_does_not_mutate_index(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        coordinator.index(IndexMode.FULL)
        before = _indexed_paths(settings.index_path, settings.project.project_id)
        (tmp_path / "src" / "A.java").unlink()
        (tmp_path / "src" / "B.java").write_text(
            "public class B { public int value() { return 99; } }\n",
            encoding="utf-8",
        )
        report = coordinator.index(
            IndexMode.VERIFY,
            include_globs=("src",),
        )
        after = _indexed_paths(settings.index_path, settings.project.project_id)
    finally:
        supervisor.shutdown()

    assert before == after
    assert report.partial is True
    assert report.state is IndexState.READY_WITH_WARNINGS
    assert report.new_files == 0
    assert report.changed_files == 1
    assert report.removed_files == 1


def test_gitignore_change_removes_only_inside_partial_scope(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        coordinator.index(IndexMode.FULL)
    finally:
        supervisor.shutdown()

    (tmp_path / ".gitignore").write_text("src/A.java\ndocs/guide.md\n", encoding="utf-8")

    partial_coordinator, partial_supervisor, _ = _build_coordinator(
        tmp_path,
        index_name="index.db",
    )
    try:
        partial = partial_coordinator.index(IndexMode.INCREMENTAL, include_globs=("src",))
        paths_after_partial = _indexed_paths(settings.index_path, settings.project.project_id)
    finally:
        partial_supervisor.shutdown()

    full_coordinator, full_supervisor, _ = _build_coordinator(tmp_path, index_name="index.db")
    try:
        full = full_coordinator.index(IndexMode.INCREMENTAL)
        paths_after_full = _indexed_paths(settings.index_path, settings.project.project_id)
    finally:
        full_supervisor.shutdown()

    assert "src/A.java" not in paths_after_partial
    assert "docs/guide.md" in paths_after_partial
    assert partial.removed_files == 1
    assert "docs/guide.md" not in paths_after_full
    assert full.removed_files == 1


def test_full_mode_partial_rewrites_only_scoped_files(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    coordinator, supervisor, settings = _build_coordinator(tmp_path)
    try:
        coordinator.index(IndexMode.FULL)
        report = coordinator.index(IndexMode.FULL, include_globs=("src",))
    finally:
        supervisor.shutdown()

    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert paths == {"docs/guide.md", "generated/C.java", "src/A.java", "src/B.java"}
    assert report.partial is True
    assert report.discovered_files == 2
    assert report.scoped_discovered_files == 2
    assert report.preserved_out_of_scope_files == 2


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
        project_id: str,
        indexed_at: str,
        updates: tuple[Any, ...],
        removed_paths: tuple[str, ...],
        *,
        progress: Callable[[PersistenceProgress], None] | None = None,
    ) -> CommitFilesMetrics:
        self.write_threads.append(threading.get_ident())
        return self._delegate.commit_files(
            project_id,
            indexed_at,
            updates,
            removed_paths,
            progress=progress,
        )

    def commit_embeddings(self, embeddings: EmbeddingBatch) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.commit_embeddings(embeddings)

    def complete_run(self, run_id: int, report: IndexReport) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.complete_run(run_id, report)

    def fail_run(self, run_id: int, finished_at: str, message: str) -> None:
        self.write_threads.append(threading.get_ident())
        return self._delegate.fail_run(run_id, finished_at, message)


def test_partial_index_keeps_sqlite_writes_on_owner_thread(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    settings = Settings(
        root=tmp_path,
        index_path=tmp_path / ".code-harness" / "owner.db",
        parser_workers=4,
    )
    guard = PathGuard(tmp_path)
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
        ).index(IndexMode.FULL, include_globs=("src",))
    finally:
        supervisor.shutdown()

    assert store.write_threads
    assert set(store.write_threads) == {owner}


def test_python_api_and_tool_request_expose_partial_filters(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    harness = CodeHarness.open(tmp_path)
    try:
        full = harness.index_project(IndexMode.FULL).data
        (tmp_path / "src" / "A.java").unlink()
        partial = harness.index_project(
            IndexMode.INCREMENTAL,
            include_globs=("src",),
            exclude_globs=("generated",),
        ).data
    finally:
        harness.close()

    assert full.partial is False
    assert partial.partial is True
    assert partial.include_globs == ("src",)
    assert partial.exclude_globs == ("generated",)
    assert "src/A.java" not in _indexed_paths(
        Settings.for_root(tmp_path).index_path,
        Settings.for_root(tmp_path).project.project_id,
    )


def test_mcp_index_project_accepts_include_and_exclude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mcp")
    from code_harness.interfaces.mcp.server import create_server

    _write_fixture(tmp_path)
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_INDEX", "1")
    settings = Settings.for_root(tmp_path)

    with CodeHarness.open(tmp_path) as harness:
        harness.index_project(IndexMode.FULL)
    (tmp_path / "src" / "A.java").unlink()

    server = create_server(tmp_path)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert "index_project" in tools
    props = set(tools["index_project"].inputSchema.get("properties", {}))
    assert {"mode", "include_globs", "exclude_globs"} <= props

    result = asyncio.run(
        server.call_tool(
            "index_project",
            {
                "mode": "incremental",
                "include_globs": ["src"],
                "exclude_globs": ["generated"],
            },
        )
    )
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        payload = result[1]
    else:
        content = result[0] if isinstance(result, tuple) else result
        payload = json.loads(content[0].text)

    assert payload["data"]["partial"] is True
    assert payload["data"]["include_globs"] == ["src"]
    assert payload["data"]["exclude_globs"] == ["generated"]
    assert payload["data"]["removed_files"] == 1
    paths = _indexed_paths(settings.index_path, settings.project.project_id)
    assert "src/A.java" not in paths
    assert "docs/guide.md" in paths
    assert "generated/C.java" in paths
