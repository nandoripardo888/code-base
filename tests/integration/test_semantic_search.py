from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness import CodeHarness
from code_harness.application.dto.requests import SemanticSearchRequest
from code_harness.application.indexing import IndexCoordinator
from code_harness.application.tools.semantic_search import SemanticSearchTool
from code_harness.bootstrap.container import build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import IndexMode, IndexState, MatchType
from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.semantic import EmbeddingIdentity, Vector
from code_harness.infrastructure.embeddings import FakeEmbeddingProvider
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    PathGuard,
)
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.interfaces.cli.main import app


class CountingReader:
    def __init__(self, delegate: LocalIndexSourceReader) -> None:
        self.delegate = delegate
        self.paths: list[str] = []

    def load(self, path: str) -> IndexedSource:
        self.paths.append(path)
        return self.delegate.load(path)


class FailingEmbeddingProvider:
    @property
    def identity(self) -> EmbeddingIdentity:
        return EmbeddingIdentity("fake", "1", "failing", 4, "test")

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[Vector, ...]:
        raise EmbeddingUnavailableError("simulated provider failure")

    def embed_query(self, text: str) -> Vector:
        raise EmbeddingUnavailableError("simulated provider failure")


class FailingEmbeddingStore(SQLiteRepositoryStore):
    def commit_embeddings(self, embeddings) -> None:
        raise EmbeddingUnavailableError("simulated semantic persistence failure")


def _semantic_components(
    root: Path,
    provider: FakeEmbeddingProvider | FailingEmbeddingProvider,
) -> tuple[IndexCoordinator, SQLiteRepositoryStore, CountingReader, Settings]:
    settings = Settings.for_root(root)
    guard = PathGuard(root)
    reader = CountingReader(LocalIndexSourceReader(guard))
    store = SQLiteRepositoryStore(settings.index_path)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        reader,
        store,
        embedding_provider=provider,
        vector_index=store,
    )
    return coordinator, store, reader, settings


def test_semantic_index_reuses_cache_and_searches_current_files(
    copied_repository: Path,
) -> None:
    provider = FakeEmbeddingProvider()
    coordinator, store, reader, settings = _semantic_components(copied_repository, provider)

    first = coordinator.index(IndexMode.INCREMENTAL)
    calls_after_first = len(provider.document_calls)
    second = coordinator.index(IndexMode.INCREMENTAL)

    assert first.generated_embeddings > 0
    assert first.embedded_chunks > 0
    assert calls_after_first == 1
    assert len(provider.document_calls) == calls_after_first
    assert second.generated_embeddings == 0
    assert len(reader.paths) == first.discovered_files
    status = store.get_status(settings.project)
    assert status.semantic_schema_ready
    assert status.embedding_count == first.generated_embeddings
    assert status.embedded_chunk_count == status.chunk_count

    tool = SemanticSearchTool(settings.project, store, reader.delegate, provider, store)
    result = tool.execute(SemanticSearchRequest("agenda service", max_results=20))

    assert result.data
    assert all(hit.match_type is MatchType.SEMANTIC for hit in result.data)
    assert all(hit.snippet.file_hash for hit in result.data)
    assert all(-1.0 <= hit.score <= 1.0 for hit in result.data)


def test_model_change_embeds_stored_chunks_without_reading_sources(
    copied_repository: Path,
) -> None:
    first_provider = FakeEmbeddingProvider(model_id="first")
    coordinator, store, reader, settings = _semantic_components(copied_repository, first_provider)
    coordinator.index(IndexMode.INCREMENTAL)
    reads_after_first = len(reader.paths)
    second_provider = FakeEmbeddingProvider(model_id="second")
    second = IndexCoordinator(
        settings.project,
        LocalFileCatalog(PathGuard(copied_repository)),
        reader,
        store,
        embedding_provider=second_provider,
        vector_index=store,
    ).index(IndexMode.INCREMENTAL)

    assert second.indexed_files == 0
    assert second.generated_embeddings > 0
    assert len(reader.paths) == reads_after_first
    assert len(second_provider.document_calls) == 1


def test_semantic_search_skips_stale_results_and_applies_language_filter(
    copied_repository: Path,
) -> None:
    provider = FakeEmbeddingProvider()
    coordinator, store, _, settings = _semantic_components(copied_repository, provider)
    coordinator.index(IndexMode.INCREMENTAL)
    tool = SemanticSearchTool(
        settings.project,
        store,
        LocalIndexSourceReader(PathGuard(copied_repository)),
        provider,
        store,
    )

    python_only = tool.execute(
        SemanticSearchRequest("agenda", languages=("python",), max_results=20)
    )
    assert python_only.data
    assert all(hit.snippet.language == "python" for hit in python_only.data)

    source = copied_repository / "src" / "agenda.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# stale\n", encoding="utf-8")
    stale = tool.execute(SemanticSearchRequest("agenda", max_results=50))
    assert any("stale semantic result" in warning for warning in stale.warnings)
    assert all(hit.snippet.location.path != "src/agenda.py" for hit in stale.data)


def test_embedding_failure_degrades_index_and_python_api_exposes_search(
    copied_repository: Path,
) -> None:
    failing = FailingEmbeddingProvider()
    coordinator, store, _, settings = _semantic_components(copied_repository, failing)
    report = coordinator.index(IndexMode.INCREMENTAL)

    assert report.state is IndexState.READY_WITH_WARNINGS
    assert report.embedding_failures == 1
    lexical = CodeHarness.open(copied_repository).search_text("AgendaService")
    assert lexical.data

    provider = FakeEmbeddingProvider()
    recovered = IndexCoordinator(
        settings.project,
        LocalFileCatalog(PathGuard(copied_repository)),
        LocalIndexSourceReader(PathGuard(copied_repository)),
        store,
        embedding_provider=provider,
        vector_index=store,
    ).index(IndexMode.INCREMENTAL)
    assert recovered.state is IndexState.READY
    assert recovered.generated_embeddings > 0

    container = build_container(settings)
    semantic_tool = SemanticSearchTool(
        settings.project,
        store,
        LocalIndexSourceReader(PathGuard(copied_repository)),
        provider,
        store,
    )
    harness = CodeHarness(replace(container, semantic_search=semantic_tool))
    assert harness.semantic_search("agenda").data


def test_embedding_persistence_failure_keeps_lexical_index(
    copied_repository: Path,
) -> None:
    settings = Settings.for_root(copied_repository)
    guard = PathGuard(copied_repository)
    store = FailingEmbeddingStore(settings.index_path)
    coordinator = IndexCoordinator(
        settings.project,
        LocalFileCatalog(guard),
        LocalIndexSourceReader(guard),
        store,
        embedding_provider=FakeEmbeddingProvider(),
        vector_index=store,
    )

    report = coordinator.index(IndexMode.INCREMENTAL)
    lexical = CodeHarness.open(copied_repository).search_text("AgendaService")
    status = store.get_status(settings.project)

    assert report.state is IndexState.READY_WITH_WARNINGS
    assert report.embedding_failures == 1
    assert any("Semantic persistence unavailable" in warning for warning in report.warnings)
    assert status.file_count == report.discovered_files
    assert status.embedding_count == 0
    assert any("Semantic persistence unavailable" in warning for warning in status.warnings)
    assert lexical.data


def test_cli_semantic_search_reports_disabled_provider(copied_repository: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["--project", str(copied_repository), "search", "semantic", "agenda"],
    )

    assert result.exit_code == 4
    assert "embedding_unavailable" in result.stderr


def test_semantic_search_requires_a_ready_provider(copied_repository: Path) -> None:
    settings = Settings.for_root(copied_repository)
    store = SQLiteRepositoryStore(settings.index_path)
    tool = SemanticSearchTool(
        settings.project,
        store,
        LocalIndexSourceReader(PathGuard(copied_repository)),
        None,
        store,
    )

    with pytest.raises(EmbeddingUnavailableError):
        tool.execute(SemanticSearchRequest("agenda"))
