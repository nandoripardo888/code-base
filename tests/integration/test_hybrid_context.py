import json
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from code_harness import CodeHarness
from code_harness.application.indexing import IndexCoordinator
from code_harness.application.tools.build_context import BuildContextTool
from code_harness.application.tools.search_code import SearchCodeTool
from code_harness.application.tools.semantic_search import SemanticSearchTool
from code_harness.bootstrap.container import build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode, MatchType
from code_harness.infrastructure.embeddings import FakeEmbeddingProvider
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.interfaces.cli.main import app


def test_hybrid_search_context_and_repository_map_work_without_semantics(
    copied_repository: Path,
) -> None:
    harness = CodeHarness.open(copied_repository)
    harness.index_project()

    hybrid = harness.search_code("AgendaService")
    context = harness.build_context(
        "AgendaService",
        max_tokens=120,
        max_snippets=5,
    )
    repository_map = harness.get_repository_map(max_files=20)

    assert hybrid.data
    assert hybrid.data[0].snippet.location.path == "src/AgendaService.java"
    assert MatchType.SYMBOL in {item.match_type for item in hybrid.data[0].evidence}
    assert context.data.snippets
    assert context.data.estimated_tokens <= context.data.available_tokens
    assert repository_map.data.root.directories
    source_directory = next(
        item for item in repository_map.data.root.directories if item.path == "src"
    )
    java_file = next(item for item in source_directory.files if item.name == "AgendaService.java")
    assert any(symbol.name == "AgendaService" for symbol in java_file.symbols)


def test_mixed_query_combines_semantic_and_exact_structural_evidence(
    copied_repository: Path,
) -> None:
    CodeHarness.open(copied_repository).index_project()
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    reader = LocalIndexSourceReader(guard)
    store = SQLiteRepositoryStore(settings.index_path)
    provider = FakeEmbeddingProvider()
    IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        reader,
        store,
        embedding_provider=provider,
        vector_index=store,
    ).index(IndexMode.INCREMENTAL)
    container = build_container(settings)
    semantic = SemanticSearchTool(settings.project, store, reader, provider, store)
    hybrid_tool = SearchCodeTool(
        container.search_text,
        container.find_symbol,
        container.find_references,
        semantic,
        container.search_files,
        reader,
    )
    context_tool = BuildContextTool(hybrid_tool, settings.project, store, reader)
    harness = CodeHarness(
        replace(
            container,
            semantic_search=semantic,
            search_code=hybrid_tool,
            build_context=context_tool,
        )
    )

    result = harness.search_code("como AgendaService coordena a agenda")
    evidence = {item.match_type for hit in result.data for item in hit.evidence}

    assert MatchType.SEMANTIC in evidence
    assert MatchType.SYMBOL in evidence


def test_hybrid_search_skips_stale_structural_candidates(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)
    harness.index_project()
    source = copied_repository / "src" / "AgendaService.java"
    source.write_text(source.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")

    result = harness.search_code("AgendaService")

    assert any("stale" in warning for warning in result.warnings)
    assert all(
        not (
            hit.snippet.location.path == "src/AgendaService.java"
            and any(item.match_type is MatchType.SYMBOL for item in hit.evidence)
        )
        for hit in result.data
    )


def test_hybrid_search_applies_file_and_language_filters(copied_repository: Path) -> None:
    harness = CodeHarness.open(copied_repository)
    harness.index_project()

    result = harness.search_code(
        "montar_agenda_consultor",
        include_globs=("*.py",),
        languages=("python",),
    )

    assert result.data
    assert all(hit.snippet.location.path.endswith(".py") for hit in result.data)
    assert all(hit.snippet.language == "python" for hit in result.data)


def test_cli_exposes_hybrid_context_and_map(copied_repository: Path) -> None:
    runner = CliRunner()
    project = str(copied_repository)
    assert runner.invoke(app, ["--project", project, "index"]).exit_code == 0

    hybrid = runner.invoke(
        app,
        ["--project", project, "--output", "json", "search", "hybrid", "AgendaService"],
    )
    context = runner.invoke(
        app,
        [
            "--project",
            project,
            "--output",
            "json",
            "context",
            "AgendaService",
            "--max-tokens",
            "120",
        ],
    )
    repository_map = runner.invoke(
        app,
        ["--project", project, "--output", "json", "map", "--max-files", "20"],
    )

    assert hybrid.exit_code == 0, hybrid.output
    assert context.exit_code == 0, context.output
    assert repository_map.exit_code == 0, repository_map.output
    assert json.loads(hybrid.stdout)["data"]
    assert json.loads(context.stdout)["data"]["estimated_tokens"] <= 120
    assert json.loads(repository_map.stdout)["data"]["included_files"] > 0
