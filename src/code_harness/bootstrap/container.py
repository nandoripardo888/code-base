from __future__ import annotations

import atexit
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from code_harness.application.indexing import IndexCoordinator
from code_harness.application.review import (
    ApplyReviewFixTool,
    BuildReviewContextTool,
    CreateReviewCommitTool,
    FindChangeImpactsTool,
    GetChangedSymbolsTool,
    GetChangeSetTool,
    ListChangedFilesTool,
    PublishReviewTool,
    ReadDiffTool,
    SuggestValidationPlanTool,
    ValidateChangeSetTool,
)
from code_harness.application.review.review_action_authorizer import ReviewActionAuthorizer
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
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.bootstrap.settings import Settings
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.structural_analyzer import StructuralAnalyzer
from code_harness.infrastructure.diagnostics import LocalDiagnosticProvider
from code_harness.infrastructure.diagnostics.capability_reporter import LocalCapabilityReporter
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
from code_harness.infrastructure.git import LocalGitChangeProvider
from code_harness.infrastructure.parsers import NativeParserSupervisor, StructuralAnalyzerRegistry
from code_harness.infrastructure.persistence import SQLiteRepositoryStore
from code_harness.infrastructure.persistence.fts_searcher import IndexedTextSearcher
from code_harness.infrastructure.ripgrep import RipgrepSearcher

if TYPE_CHECKING:
    from code_harness.bootstrap.changes import ChangeSessionContainer
    from code_harness.bootstrap.execution import ExecutionContainer


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    project: Project
    store: RepositoryStore
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
    get_change_set: GetChangeSetTool
    list_changed_files: ListChangedFilesTool
    read_diff: ReadDiffTool
    get_changed_symbols: GetChangedSymbolsTool
    find_change_impacts: FindChangeImpactsTool
    build_review_context: BuildReviewContextTool
    suggest_validation_plan: SuggestValidationPlanTool
    validate_change_set: ValidateChangeSetTool | None = None
    apply_review_fix: ApplyReviewFixTool | None = None
    publish_review: PublishReviewTool | None = None
    create_review_commit: CreateReviewCommitTool | None = None
    execution: ExecutionContainer | None = None
    changes: ChangeSessionContainer | None = None
    _analyzer: StructuralAnalyzer | None = field(default=None, repr=False, compare=False)
    _embedding_provider: EmbeddingProvider | None = field(default=None, repr=False, compare=False)
    _shutdown_done: list[bool] = field(default_factory=lambda: [False], repr=False, compare=False)

    def with_index_state[T](self, result: ToolResult[T]) -> ToolResult[T]:
        if result.index_state is not None:
            return result
        return replace(
            result,
            index_state=resolve_index_state(self.store, self.project),
        )

    def shutdown(self) -> None:
        if self._shutdown_done[0]:
            return
        self._shutdown_done[0] = True
        if self.execution is not None:
            self.execution.shutdown()
        if self._analyzer is not None:
            self._analyzer.shutdown()
        provider = self._embedding_provider
        if provider is not None:
            shutdown = getattr(provider, "shutdown", None)
            if callable(shutdown):
                shutdown()

    def __enter__(self) -> ApplicationContainer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()


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
        failure_window_seconds=settings.parser_failure_window_seconds,
        circuit_reset_seconds=settings.parser_circuit_reset_seconds,
        worker_count=settings.parser_workers,
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
        parser_workers=settings.parser_workers,
    )
    capability_reporter = LocalCapabilityReporter(
        semantic_enabled=settings.semantic_enabled,
        semantic_model_id=settings.embedding_model if settings.semantic_enabled else None,
        ripgrep_executable=settings.ripgrep_executable,
        model_cache_path=settings.embedding_cache_path,
    )
    diagnostics = LocalDiagnosticProvider(
        settings.root,
        settings.index_path,
        settings.ripgrep_executable,
        analyzer,
        embedding_provider,
        settings.semantic_enabled,
        settings.embedding_cache_path,
        capability_reporter=capability_reporter,
        execution_enabled=settings.execution_enabled,
        execution_backend=settings.execution_backend,
        execution_home=settings.execution_project_home(),
        execution_store_path=settings.execution_store_path(),
        execution_allow_elevated=settings.execution_allow_elevated,
        execution_powershell_enabled=settings.execution_powershell_enabled,
        execution_powershell_executable=settings.execution_powershell_executable,
        mcp_expose_execution=settings.mcp_expose_execution,
    )
    search_files_tool = SearchFilesTool(catalog, project=project, store=store)
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
        project=project,
        store=store,
    )
    change_provider = LocalGitChangeProvider(settings.root, repository_id=project.project_id)
    get_change_set_tool = GetChangeSetTool(change_provider)
    list_changed_files_tool = ListChangedFilesTool(change_provider)
    read_diff_tool = ReadDiffTool(change_provider)
    get_changed_symbols_tool = GetChangedSymbolsTool(
        project=project,
        store=store,
        provider=change_provider,
    )
    find_change_impacts_tool = FindChangeImpactsTool(
        changed_symbols=get_changed_symbols_tool,
        find_references=find_references_tool,
        search_files=search_files_tool,
    )
    build_review_context_tool = BuildReviewContextTool(
        reader=index_reader,
        read_diff=read_diff_tool,
        changed_symbols=get_changed_symbols_tool,
        find_impacts=find_change_impacts_tool,
    )
    suggest_validation_plan_tool = SuggestValidationPlanTool(
        catalog=catalog,
        list_changed_files=list_changed_files_tool,
        find_impacts=find_change_impacts_tool,
    )
    execution = _build_execution_container(settings, project, guard)
    changes = _build_change_session_container(settings, project)
    validate_change_set_tool: ValidateChangeSetTool | None = None
    apply_review_fix_tool: ApplyReviewFixTool | None = None
    publish_review_tool: PublishReviewTool | None = None
    create_review_commit_tool: CreateReviewCommitTool | None = None
    if settings.review_actions_enabled:
        from code_harness.infrastructure.git.git_review_committer import GitReviewCommitter
        from code_harness.infrastructure.git.isolated_worktree import IsolatedGitWorktreeFactory
        from code_harness.infrastructure.git.unified_patch_applier import UnifiedPatchApplier
        from code_harness.infrastructure.git.workspace_snapshot import (
            GitWorkspaceSnapshotProvider,
        )
        from code_harness.infrastructure.review.file_review_publisher import FileReviewPublisher

        snapshots = GitWorkspaceSnapshotProvider(settings.root)
        authorizer = None
        run_process = None
        if execution is not None:
            authorizer = ReviewActionAuthorizer(
                project_id=project.project_id,
                store=execution.approval_store,
                require_approval=settings.execution_require_approval,
                approval_ttl_seconds=settings.execution_approval_ttl_seconds,
                backend=settings.execution_backend,
            )
            run_process = execution.run_process
        validate_change_set_tool = ValidateChangeSetTool(
            provider=change_provider,
            snapshots=snapshots,
            run_process=run_process,
            project_root=settings.root,
            worktree_factory=IsolatedGitWorktreeFactory(settings.root),
            enabled=True,
            default_use_worktree=settings.review_use_worktree,
        )
        apply_review_fix_tool = ApplyReviewFixTool(
            provider=change_provider,
            applier=UnifiedPatchApplier(
                settings.root,
                change_provider=change_provider,
                snapshot_provider=snapshots,
            ),
            authorizer=authorizer,
            enabled=True,
            allowed=settings.review_allow_apply,
            project_id=project.project_id,
        )
        publish_dir = settings.review_publish_dir or (
            settings.execution_project_home() / "review_publications"
        )
        publish_review_tool = PublishReviewTool(
            provider=change_provider,
            publisher=FileReviewPublisher(publish_dir) if settings.review_allow_publish else None,
            authorizer=authorizer,
            enabled=True,
            allowed=settings.review_allow_publish,
            project_id=project.project_id,
        )
        create_review_commit_tool = CreateReviewCommitTool(
            provider=change_provider,
            snapshots=snapshots,
            committer=GitReviewCommitter(settings.root) if settings.review_allow_commit else None,
            authorizer=authorizer,
            enabled=True,
            allowed=settings.review_allow_commit,
            project_id=project.project_id,
        )
    container = ApplicationContainer(
        project=project,
        store=store,
        initialize_index=InitializeIndexTool(project, store),
        index_project=IndexProjectTool(coordinator),
        get_index_status=GetIndexStatusTool(
            project,
            store,
            settings.embedding_model if settings.semantic_enabled else None,
            capability_reporter=capability_reporter,
            build_commit=settings.build_commit,
            service_started_at=settings.service_started_at,
            service_instance_id=settings.service_instance_id,
        ),
        doctor=DoctorTool(diagnostics),
        list_files=ListFilesTool(catalog, project=project, store=store),
        search_files=search_files_tool,
        search_text=search_text_tool,
        search_regex=SearchRegexTool(searcher),
        read_file=ReadFileTool(reader, project=project, store=store),
        read_range=ReadRangeTool(reader, project=project, store=store),
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
        get_change_set=get_change_set_tool,
        list_changed_files=list_changed_files_tool,
        read_diff=read_diff_tool,
        get_changed_symbols=get_changed_symbols_tool,
        find_change_impacts=find_change_impacts_tool,
        build_review_context=build_review_context_tool,
        suggest_validation_plan=suggest_validation_plan_tool,
        validate_change_set=validate_change_set_tool,
        apply_review_fix=apply_review_fix_tool,
        publish_review=publish_review_tool,
        create_review_commit=create_review_commit_tool,
        execution=execution,
        changes=changes,
        _analyzer=analyzer,
        _embedding_provider=embedding_provider,
    )
    atexit.register(container.shutdown)
    return container


def _build_execution_container(
    settings: Settings,
    project: Project,
    guard: PathGuard,
) -> ExecutionContainer | None:
    if not settings.execution_enabled:
        return None
    from code_harness.bootstrap.execution import build_execution_container

    return build_execution_container(settings, project, guard)


def _build_change_session_container(
    settings: Settings,
    project: Project,
) -> ChangeSessionContainer:
    from code_harness.bootstrap.changes import build_change_session_container

    return build_change_session_container(settings, project)


@asynccontextmanager
async def container_lifespan(_app: Any) -> AsyncIterator[dict[str, Any]]:
    """FastMCP lifespan hook; concrete servers bind their own container shutdown."""
    yield {}
