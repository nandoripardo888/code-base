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
from code_harness.domain.models.tool_result import warning_message
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
    repository_map = harness.get_repository_map(max_files=20, mode="detailed")

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

    assert any("stale" in warning_message(warning) for warning in result.warnings)
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


def test_hybrid_search_bounds_large_structural_symbols(copied_repository: Path) -> None:
    source = copied_repository / "src" / "LargeType.java"
    lines = ["public class LargeType {\n"]
    lines.extend(f"    private int filler{index};\n" for index in range(1, 1_202))
    lines[899] = '    private String universalNeedle = "universalNeedle";\n'
    lines[900] = "    public void universalNeedleHandler() {}\n"
    lines.append("}\n")
    source.write_text("".join(lines), encoding="utf-8")
    harness = CodeHarness.open(copied_repository)
    harness.index_project()

    result = harness.search_code(
        "LargeType universalNeedle",
        include_globs=("src/LargeType.java",),
        max_results=5,
    )

    assert result.data
    assert any("universalNeedle" in hit.snippet.content for hit in result.data)
    assert all(len(hit.snippet.content) <= 6_000 for hit in result.data)
    assert all(
        hit.snippet.location.end_line - hit.snippet.location.start_line + 1 <= 40
        for hit in result.data
    )
    truncated = next(hit for hit in result.data if hit.snippet_truncated)
    assert truncated.source_location is not None
    assert truncated.source_location.end_line >= 1_200


def test_anchor_ranking_and_context_enumeration_stay_in_resolved_container(
    copied_repository: Path,
) -> None:
    source = copied_repository / "src" / "WORK_ORDER_CURSOR.java"
    lines = ["public class WORK_ORDER_CURSOR {\n"]
    for number in range(1, 31):
        lines.append(f"  Object item{number};\n")
        lines.append(f'  void configure{number}() {{ setName("FIELD_{number}"); }}\n')
        lines.extend(f"  // filler {number}-{index}\n" for index in range(12))
    lines.append(
        '  public void setFilterTECHNICIAN_CODE(Object value) { setName("TECHNICIAN_CODE"); }\n'
    )
    lines.extend(f"  int tail{index};\n" for index in range(800))
    lines.append("}\n")
    source.write_text("".join(lines), encoding="utf-8")
    (copied_repository / "src" / "WORK_ORDER_CURSORRowType.java").write_text(
        "/** WORK_ORDER_CURSOR TECHNICIAN_CODE DOCUMENTATION_ONLY_TOKEN. */\n"
        "public class WORK_ORDER_CURSORRowType {}\n",
        encoding="utf-8",
    )
    (copied_repository / "src" / "GenericFields.java").write_text(
        "public class GenericFields {\n  void getTECHNICIAN_CODE() {}\n}\n",
        encoding="utf-8",
    )
    harness = CodeHarness.open(copied_repository)
    harness.index_project()

    search = harness.search_code(
        "WORK_ORDER_CURSOR TECHNICIAN_CODE",
        max_results=50,
    )
    comment_search = harness.search_code("DOCUMENTATION_ONLY_TOKEN")
    small = harness.build_context(
        "Quais campos o cursor WORK_ORDER_CURSOR possui?",
        max_tokens=1_800,
        max_expansion_depth=0,
    )
    large = harness.build_context(
        "Quais campos o cursor WORK_ORDER_CURSOR possui?",
        max_tokens=6_000,
        max_expansion_depth=0,
    )

    assert search.data[0].snippet.location.path == "src/WORK_ORDER_CURSOR.java"
    assert "TECHNICIAN_CODE" in search.data[0].snippet.content
    assert search.data[0].score < 1.0
    assert comment_search.data
    assert comment_search.data[0].comment_only is True
    assert comment_search.data[0].score <= 0.35
    assert small.data.snippets
    assert large.data.snippets
    assert all(
        item.snippet.location.path == "src/WORK_ORDER_CURSOR.java" for item in small.data.snippets
    )
    assert all(
        item.snippet.location.path == "src/WORK_ORDER_CURSOR.java" for item in large.data.snippets
    )
    small_fields = sum(item.snippet.content.count("FIELD_") for item in small.data.snippets)
    large_fields = sum(item.snippet.content.count("FIELD_") for item in large.data.snippets)
    assert large_fields > small_fields
    assert small.data.considered_results == (
        small.data.selected_results + small.data.omitted_results
    )
    assert large.data.considered_results == (
        large.data.selected_results + large.data.omitted_results
    )


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
