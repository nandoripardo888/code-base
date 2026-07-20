from dataclasses import dataclass

from code_harness.application.indexing import IndexCoordinator
from code_harness.application.tools import (
    BuildContextTool,
    DoctorTool,
    FindDefinitionTool,
    FindReferencesTool,
    FindSymbolTool,
    GetFileOutlineTool,
    GetIndexStatusTool,
    GetRepositoryMapTool,
    IndexProjectTool,
    InitializeIndexTool,
    ListFilesTool,
    PrepareSemanticModelTool,
    ReadFileTool,
    ReadRangeTool,
    SearchCodeTool,
    SearchFilesTool,
    SearchRegexTool,
    SearchTextTool,
    SemanticSearchTool,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider
from code_harness.infrastructure.diagnostics import LocalDiagnosticProvider
from code_harness.infrastructure.embeddings import (
    NativeEmbeddingSupervisor,
    UnavailableEmbeddingProvider,
)
from code_harness.infrastructure.filesystem import (
    LocalFileCatalog,
    LocalIndexSourceReader,
    LocalSourceReader,
    PathGuard,
)
from code_harness.infrastructure.parsers import NativeParserSupervisor, StructuralAnalyzerRegistry
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.infrastructure.persistence.fts_searcher import IndexedTextSearcher
from code_harness.infrastructure.ripgrep import RipgrepSearcher


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    initialize_index: InitializeIndexTool
    index_project: IndexProjectTool
    get_index_status: GetIndexStatusTool
    doctor: DoctorTool
    list_files: ListFilesTool
    search_files: SearchFilesTool
    search_text: SearchTextTool
    search_regex: SearchRegexTool
    read_file: ReadFileTool
    read_range: ReadRangeTool
    get_file_outline: GetFileOutlineTool
    find_symbol: FindSymbolTool
    find_definition: FindDefinitionTool
    find_references: FindReferencesTool
    semantic_search: SemanticSearchTool
    search_code: SearchCodeTool
    build_context: BuildContextTool
    get_repository_map: GetRepositoryMapTool
    prepare_semantic_model: PrepareSemanticModelTool


def build_container(settings: Settings) -> ApplicationContainer:
    guard = PathGuard(settings.root)
    catalog = LocalFileCatalog(guard)
    reader = LocalSourceReader(guard, max_file_size_bytes=settings.max_file_size_bytes)
    index_reader = LocalIndexSourceReader(guard, max_file_size_bytes=settings.max_file_size_bytes)
    ripgrep_searcher = RipgrepSearcher(
        guard.root,
        reader,
        executable=settings.ripgrep_executable,
        max_file_size_bytes=settings.max_file_size_bytes,
    )
    project = settings.project
    store = SQLiteRepositoryStore(settings.index_path)
    embedding_provider: EmbeddingProvider | None = None
    if settings.semantic_enabled:
        if settings.embedding_provider == "local":
            embedding_provider = NativeEmbeddingSupervisor(
                settings.embedding_model,
                batch_size=settings.embedding_batch_size,
                window_chars=settings.embedding_window_chars,
                window_overlap_chars=settings.embedding_window_overlap_chars,
                cache_dir=settings.embedding_cache_path,
                timeout_seconds=settings.embedding_timeout_seconds,
                system_trust=settings.system_trust_enabled,
                ca_bundle_path=settings.ca_bundle_path,
            )
        else:
            embedding_provider = UnavailableEmbeddingProvider(
                f"Unsupported embedding provider: {settings.embedding_provider}."
            )
    parser_supervisor = NativeParserSupervisor(
        enabled=settings.parsers_enabled,
        timeout_seconds=settings.parser_timeout_seconds,
        failure_threshold=settings.parser_failure_threshold,
        circuit_reset_seconds=settings.parser_circuit_reset_seconds,
    )
    analyzer = StructuralAnalyzerRegistry((parser_supervisor,))
    searcher = IndexedTextSearcher(project, store, index_reader, ripgrep_searcher)
    coordinator = IndexCoordinator(
        project,
        catalog,
        index_reader,
        store,
        analyzer=analyzer,
        embedding_provider=embedding_provider,
        vector_index=store,
        chunk_target_chars=settings.chunk_target_chars,
        chunk_max_chars=settings.chunk_max_chars,
    )
    diagnostics = LocalDiagnosticProvider(
        settings.root,
        settings.index_path,
        settings.ripgrep_executable,
        analyzer,
        embedding_provider,
        settings.semantic_enabled,
        settings.embedding_cache_path,
    )
    search_files_tool = SearchFilesTool(catalog)
    search_text_tool = SearchTextTool(searcher)
    find_symbol_tool = FindSymbolTool(project, store, index_reader)
    find_references_tool = FindReferencesTool(project, store, index_reader, ripgrep_searcher)
    semantic_search_tool = SemanticSearchTool(
        project,
        store,
        index_reader,
        embedding_provider,
        store,
    )
    search_code_tool = SearchCodeTool(
        search_text_tool,
        find_symbol_tool,
        find_references_tool,
        semantic_search_tool,
        search_files_tool,
        index_reader,
    )
    return ApplicationContainer(
        initialize_index=InitializeIndexTool(project, store),
        index_project=IndexProjectTool(coordinator),
        get_index_status=GetIndexStatusTool(
            project,
            store,
            settings.embedding_model if settings.semantic_enabled else None,
        ),
        doctor=DoctorTool(diagnostics),
        list_files=ListFilesTool(catalog),
        search_files=search_files_tool,
        search_text=search_text_tool,
        search_regex=SearchRegexTool(searcher),
        read_file=ReadFileTool(reader),
        read_range=ReadRangeTool(reader),
        get_file_outline=GetFileOutlineTool(project, store, index_reader),
        find_symbol=find_symbol_tool,
        find_definition=FindDefinitionTool(project, store, index_reader),
        find_references=find_references_tool,
        semantic_search=semantic_search_tool,
        search_code=search_code_tool,
        build_context=BuildContextTool(search_code_tool, project, store, index_reader),
        get_repository_map=GetRepositoryMapTool(
            project,
            catalog,
            store,
            index_reader,
        ),
        prepare_semantic_model=PrepareSemanticModelTool(
            embedding_provider,
            settings.embedding_cache_path,
        ),
    )
