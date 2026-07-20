import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness import CodeHarness
from code_harness.application.indexing import IndexCoordinator
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode, IndexState, ParseState
from code_harness.domain.errors import ParserCrashError
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.structural import AnalyzeRequest, AnalyzeResult, CodeReference
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.parsers import NativeParserSupervisor
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.interfaces.cli.main import app

runner = CliRunner()


class FailingAnalyzer:
    name = "failing"
    version = "1"

    def supports(self, language: str) -> bool:
        return language in {"java", "python", "plsql"}

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        raise ParserCrashError(request.path, "simulated worker crash")

    def health_check(self) -> bool:
        return False

    def shutdown(self) -> None:
        return None


class CountingAnalyzer:
    def __init__(self) -> None:
        self.delegate = NativeParserSupervisor(timeout_seconds=5)
        self.calls = 0

    @property
    def name(self) -> str:
        return self.delegate.name

    @property
    def version(self) -> str:
        return self.delegate.version

    def supports(self, language: str) -> bool:
        return self.delegate.supports(language)

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        self.calls += 1
        return self.delegate.analyze(request)

    def health_check(self) -> bool:
        return self.delegate.health_check()

    def shutdown(self) -> None:
        self.delegate.shutdown()


class DuplicateReferenceAnalyzer:
    name = "duplicate-reference"
    version = "1"

    def supports(self, language: str) -> bool:
        return language in {"java", "python", "plsql"}

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        location = CodeLocation(request.path, 1, 1, 1)
        reference = CodeReference("duplicate", "call", "call", location)
        return AnalyzeResult(
            parser_name=self.name,
            parser_version=self.version,
            state=ParseState.READY,
            references=(reference, reference),
        )

    def health_check(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None


def test_index_persists_structure_and_exposes_python_api(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)

    report = harness.index_project().data
    status = harness.get_index_status().data
    outline = harness.get_file_outline("src/AgendaService.java")
    symbols = harness.find_symbol("AgendaService")
    definition = harness.find_definition("validarAgenda")
    references = harness.find_references("validarAgenda")

    assert report.indexed_symbols >= 6
    assert report.indexed_chunks >= report.indexed_symbols
    assert status.state is IndexState.READY
    assert status.symbol_count == report.indexed_symbols
    assert status.chunk_count == report.indexed_chunks
    assert {item.symbol.name for item in outline.data if item.symbol} >= {
        "AgendaService",
        "montarAgendaConsultor",
        "validarAgenda",
    }
    assert symbols.data[0].content.startswith("public class AgendaService")
    assert definition.data[0].symbol is not None
    assert references.data[0].reference is not None
    assert "validarAgenda()" in references.data[0].content


def test_structural_results_are_skipped_after_file_changes(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)
    harness.index_project()
    source = copied_repository / "src" / "agenda.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# stale\n", encoding="utf-8")

    result = harness.find_symbol("montar_agenda_consultor")

    assert all(
        item.symbol is None or item.symbol.location.path != "src/agenda.py" for item in result.data
    )
    assert any("stale" in warning for warning in result.warnings)


def test_references_fall_back_to_lexical_search_without_index(
    copied_repository: Path,
) -> None:
    result = CodeHarness.open(copied_repository).find_references("AgendaService")

    assert result.data
    assert any(item.reference and item.reference.kind == "textual" for item in result.data)
    assert any("lexical" in warning for warning in result.warnings)


def test_cli_exposes_structural_commands(copied_repository: Path) -> None:
    project = str(copied_repository)
    indexed = runner.invoke(app, ["--project", project, "index"])
    outline = runner.invoke(
        app,
        ["--project", project, "--output", "json", "outline", "src/AgendaService.java"],
    )
    symbols = runner.invoke(
        app,
        ["--project", project, "--output", "json", "search", "symbol", "AgendaService"],
    )

    assert indexed.exit_code == 0, indexed.output
    assert outline.exit_code == 0, outline.output
    assert json.loads(outline.stdout)["data"]
    assert symbols.exit_code == 0, symbols.output
    assert json.loads(symbols.stdout)["data"]


def test_disabled_parser_preserves_lexical_indexing(
    copied_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_PARSERS", "0")
    harness = CodeHarness.open(copied_repository)
    report = harness.index_project().data
    lexical = harness.search_text("AgendaService")

    assert report.state is IndexState.READY
    assert report.indexed_symbols == 0
    assert report.indexed_chunks > 0
    assert lexical.data


def test_parser_failure_sets_warning_and_preserves_lexical_search(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=FailingAnalyzer(),
    )

    report = coordinator.index(IndexMode.INCREMENTAL)
    harness = CodeHarness.open(copied_repository)
    status = harness.get_index_status().data
    lexical = harness.search_text("AgendaService")

    assert report.state is IndexState.READY_WITH_WARNINGS
    assert report.parser_failures == 3
    assert status.parser_failure_count == 3
    assert lexical.data


def test_invalid_structural_payload_falls_back_per_file(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=DuplicateReferenceAnalyzer(),
    )

    report = coordinator.index(IndexMode.INCREMENTAL)
    lexical = CodeHarness.open(copied_repository).search_text("AgendaService")

    assert report.state is IndexState.READY_WITH_WARNINGS
    assert any("duplicate reference_id" in warning for warning in report.warnings)
    assert report.parser_failures == 3
    assert lexical.data


def test_unchanged_incremental_index_does_not_parse_again(copied_repository: Path) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    analyzer = CountingAnalyzer()
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        SQLiteRepositoryStore(settings.index_path),
        analyzer=analyzer,
    )

    coordinator.index(IndexMode.INCREMENTAL)
    first_calls = analyzer.calls
    second = coordinator.index(IndexMode.INCREMENTAL)
    analyzer.shutdown()

    assert first_calls == 3
    assert second.indexed_files == 0
    assert analyzer.calls == first_calls
