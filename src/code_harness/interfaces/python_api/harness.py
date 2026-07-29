from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from code_harness.application.dto.execution_requests import (
    GetExecutionRequest,
    InspectPowerShellRequest,
    InspectProcessRequest,
    RunPowerShellRequest,
    RunProcessRequest,
    TerminateExecutionRequest,
)
from code_harness.application.dto.requests import (
    BuildContextRequest,
    FindDefinitionRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetFileOutlineRequest,
    GetRepositoryMapRequest,
    IndexProjectRequest,
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchCodeRequest,
    SearchFilesRequest,
    SearchRegexRequest,
    SearchTextRequest,
    SemanticSearchRequest,
)
from code_harness.application.dto.review_requests import (
    ApplyReviewFixRequest,
    BuildReviewContextRequest,
    CreateReviewCommitRequest,
    FindChangeImpactsRequest,
    GetChangedSymbolsRequest,
    GetChangeSetRequest,
    ListChangedFilesRequest,
    PublishReviewRequest,
    ReadDiffRequest,
    SuggestValidationPlanRequest,
    ValidateChangeSetRequest,
)
from code_harness.bootstrap.container import ApplicationContainer, build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ApprovalState, ExecutionCapability, IndexMode
from code_harness.domain.errors import ExecutionDisabledError, ReviewActionsDisabledError
from code_harness.domain.models.change_set import ChangedFile, ChangeDiff, ChangedSymbol, ChangeSet
from code_harness.domain.models.code_chunk import SourceRead
from code_harness.domain.models.context import ContextBundle
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionApproval,
    ExecutionResult,
)
from code_harness.domain.models.file_listing import FileListingPage
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.index_report import DoctorReport, IndexReport, IndexStatus
from code_harness.domain.models.project import Project
from code_harness.domain.models.repository_map import RepositoryMap
from code_harness.domain.models.review import ChangeImpact, ReviewContextBundle, ValidationPlan
from code_harness.domain.models.review_actions import (
    PublishedReview,
    ReviewCommitResult,
    ReviewFixApplication,
    ValidationRunResult,
)
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.semantic import SemanticPreparationReport
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult

if TYPE_CHECKING:
    from code_harness.bootstrap.execution import ExecutionContainer


class CodeHarness:
    def __init__(self, container: ApplicationContainer) -> None:
        self._container = container

    @classmethod
    def open(cls, root: str | Path) -> CodeHarness:
        return cls(build_container(Settings.for_root(root)))

    def close(self) -> None:
        self._container.shutdown()

    def __enter__(self) -> CodeHarness:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _execution(self) -> ExecutionContainer:
        if self._container.execution is None:
            raise ExecutionDisabledError()
        return self._container.execution

    def inspect_process(
        self,
        executable: str,
        args: tuple[str, ...] = (),
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: tuple[ExecutionCapability | str, ...] = (),
        reason: str | None = None,
    ) -> ToolResult[CommandInspection]:
        capabilities = tuple(
            item if isinstance(item, ExecutionCapability) else ExecutionCapability(item)
            for item in requested_capabilities
        )
        return self._execution().inspect_process.execute(
            InspectProcessRequest(
                executable,
                args,
                cwd,
                timeout_seconds,
                max_output_bytes,
                capabilities,
                reason,
            )
        )

    def inspect_powershell(
        self,
        script: str,
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: tuple[ExecutionCapability | str, ...] = (),
        reason: str | None = None,
    ) -> ToolResult[CommandInspection]:
        capabilities = tuple(
            item if isinstance(item, ExecutionCapability) else ExecutionCapability(item)
            for item in requested_capabilities
        )
        return self._execution().inspect_powershell.execute(
            InspectPowerShellRequest(
                script,
                cwd,
                timeout_seconds,
                max_output_bytes,
                capabilities,
                reason,
            )
        )

    def run_process(
        self,
        executable: str,
        args: tuple[str, ...] = (),
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: tuple[ExecutionCapability | str, ...] = (),
        reason: str | None = None,
        approval_id: str | None = None,
        wait: bool = True,
    ) -> ToolResult[ExecutionResult]:
        capabilities = tuple(
            item if isinstance(item, ExecutionCapability) else ExecutionCapability(item)
            for item in requested_capabilities
        )
        return self._execution().run_process.execute(
            RunProcessRequest(
                executable=executable,
                args=args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                requested_capabilities=capabilities,
                reason=reason,
                approval_id=approval_id,
                wait=wait,
            )
        )

    def run_powershell(
        self,
        script: str,
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: tuple[ExecutionCapability | str, ...] = (),
        reason: str | None = None,
        approval_id: str | None = None,
        wait: bool = True,
    ) -> ToolResult[ExecutionResult]:
        capabilities = tuple(
            item if isinstance(item, ExecutionCapability) else ExecutionCapability(item)
            for item in requested_capabilities
        )
        return self._execution().run_powershell.execute(
            RunPowerShellRequest(
                script=script,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                requested_capabilities=capabilities,
                reason=reason,
                approval_id=approval_id,
                wait=wait,
            )
        )

    def get_execution(
        self,
        execution_id: str,
        *,
        include_output: bool = True,
    ) -> ToolResult[ExecutionResult]:
        return self._execution().get_execution.execute(
            GetExecutionRequest(execution_id, include_output)
        )

    def terminate_execution(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> ToolResult[ExecutionResult]:
        return self._execution().terminate_execution.execute(
            TerminateExecutionRequest(execution_id, reason)
        )

    def list_execution_approvals(
        self,
        *,
        state: ApprovalState | str | None = None,
        limit: int = 50,
    ) -> ToolResult[tuple[ExecutionApproval, ...]]:
        selected = ApprovalState(state) if state is not None else None
        return self._execution().approvals.list(state=selected, limit=limit)

    def get_execution_approval(
        self,
        approval_id: str,
    ) -> ToolResult[ExecutionApproval]:
        return self._execution().approvals.get(approval_id)

    def approve_execution(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
    ) -> ToolResult[ExecutionApproval]:
        return self._execution().approvals.approve(approval_id, reason=reason)

    def deny_execution(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
    ) -> ToolResult[ExecutionApproval]:
        return self._execution().approvals.deny(approval_id, reason=reason)

    def initialize_index(self) -> ToolResult[Project]:
        return self._container.initialize_index.execute()

    def index_project(
        self,
        mode: IndexMode | str = IndexMode.INCREMENTAL,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
    ) -> ToolResult[IndexReport]:
        return self._container.index_project.execute(
            IndexProjectRequest(
                IndexMode(mode),
                include_globs=include_globs,
                exclude_globs=exclude_globs,
            )
        )

    def get_index_status(self) -> ToolResult[IndexStatus]:
        return self._container.get_index_status.execute()

    def get_change_set(
        self,
        *,
        source: str = "working_tree",
        base: str | None = "HEAD",
        include_untracked: bool = True,
    ) -> ToolResult[ChangeSet]:
        return self._container.get_change_set.execute(
            GetChangeSetRequest(
                source=source,
                base=base,
                include_untracked=include_untracked,
            )
        )

    def list_changed_files(self, change_set_id: str) -> ToolResult[tuple[ChangedFile, ...]]:
        return self._container.list_changed_files.execute(
            ListChangedFilesRequest(change_set_id=change_set_id)
        )

    def read_diff(
        self,
        change_set_id: str,
        *,
        path: str | None = None,
    ) -> ToolResult[ChangeDiff]:
        return self._container.read_diff.execute(
            ReadDiffRequest(change_set_id=change_set_id, path=path)
        )

    def get_changed_symbols(
        self,
        change_set_id: str,
        *,
        path: str | None = None,
        max_symbols: int = 200,
    ) -> ToolResult[tuple[ChangedSymbol, ...]]:
        return self._container.get_changed_symbols.execute(
            GetChangedSymbolsRequest(
                change_set_id=change_set_id,
                path=path,
                max_symbols=max_symbols,
            )
        )

    def find_change_impacts(
        self,
        change_set_id: str,
        *,
        path: str | None = None,
        max_symbols: int = 50,
        max_references_per_symbol: int = 30,
        max_tests_per_symbol: int = 10,
        include_config: bool = True,
    ) -> ToolResult[tuple[ChangeImpact, ...]]:
        return self._container.find_change_impacts.execute(
            FindChangeImpactsRequest(
                change_set_id=change_set_id,
                path=path,
                max_symbols=max_symbols,
                max_references_per_symbol=max_references_per_symbol,
                max_tests_per_symbol=max_tests_per_symbol,
                include_config=include_config,
            )
        )

    def build_review_context(
        self,
        change_set_id: str,
        *,
        path: str | None = None,
        max_tokens: int = 12_000,
        reserved_tokens: int = 2_000,
        max_files: int = 15,
        max_snippets: int = 25,
    ) -> ToolResult[ReviewContextBundle]:
        return self._container.build_review_context.execute(
            BuildReviewContextRequest(
                change_set_id=change_set_id,
                path=path,
                max_tokens=max_tokens,
                reserved_tokens=reserved_tokens,
                max_files=max_files,
                max_snippets=max_snippets,
            )
        )

    def suggest_validation_plan(
        self,
        change_set_id: str,
        *,
        path: str | None = None,
        max_test_files: int = 20,
    ) -> ToolResult[ValidationPlan]:
        return self._container.suggest_validation_plan.execute(
            SuggestValidationPlanRequest(
                change_set_id=change_set_id,
                path=path,
                max_test_files=max_test_files,
            )
        )

    def _review_actions(self) -> None:
        if self._container.validate_change_set is None:
            raise ReviewActionsDisabledError()

    def validate_change_set(
        self,
        change_set_id: str,
        executable: str,
        args: tuple[str, ...] = (),
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: tuple[str, ...] = (),
        reason: str | None = None,
        approval_id: str | None = None,
        wait: bool = True,
        use_worktree: bool | None = None,
    ) -> ToolResult[ValidationRunResult]:
        self._review_actions()
        assert self._container.validate_change_set is not None
        return self._container.validate_change_set.execute(
            ValidateChangeSetRequest(
                change_set_id=change_set_id,
                executable=executable,
                args=args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                requested_capabilities=requested_capabilities,
                reason=reason,
                approval_id=approval_id,
                wait=wait,
                use_worktree=use_worktree,
            )
        )

    def apply_review_fix(
        self,
        change_set_id: str,
        patch_text: str,
        expected_file_hashes: tuple[tuple[str, str], ...],
        *,
        approval_id: str | None = None,
        reason: str | None = None,
    ) -> ToolResult[ReviewFixApplication]:
        self._review_actions()
        assert self._container.apply_review_fix is not None
        return self._container.apply_review_fix.execute(
            ApplyReviewFixRequest(
                change_set_id=change_set_id,
                patch_text=patch_text,
                expected_file_hashes=expected_file_hashes,
                approval_id=approval_id,
                reason=reason,
            )
        )

    def publish_review(
        self,
        change_set_id: str,
        title: str,
        *,
        body: str = "",
        comments: tuple[tuple[str, str, int | None, int | None, str], ...] = (),
        approval_id: str | None = None,
    ) -> ToolResult[PublishedReview]:
        self._review_actions()
        assert self._container.publish_review is not None
        return self._container.publish_review.execute(
            PublishReviewRequest(
                change_set_id=change_set_id,
                title=title,
                body=body,
                comments=comments,
                approval_id=approval_id,
            )
        )

    def create_review_commit(
        self,
        change_set_id: str,
        message: str,
        *,
        paths: tuple[str, ...] | None = None,
        approval_id: str | None = None,
    ) -> ToolResult[ReviewCommitResult]:
        self._review_actions()
        assert self._container.create_review_commit is not None
        return self._container.create_review_commit.execute(
            CreateReviewCommitRequest(
                change_set_id=change_set_id,
                message=message,
                paths=paths,
                approval_id=approval_id,
            )
        )

    def doctor(self, *, deep: bool = False) -> ToolResult[DoctorReport]:
        return self._container.doctor.execute(deep=deep)

    def prepare_semantic_model(self) -> ToolResult[SemanticPreparationReport]:
        return self._container.prepare_semantic_model.execute()

    def get_file_outline(
        self,
        path: str,
        *,
        include_content: bool | None = None,
        response_format: str = "compact",
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.get_file_outline.execute(
            GetFileOutlineRequest(
                path,
                include_content=include_content,
                response_format=response_format,
            )
        )

    def find_symbol(
        self,
        query: str,
        *,
        max_results: int = 50,
        exact: bool = False,
        include_content: bool | None = None,
        response_format: str = "compact",
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_symbol.execute(
            FindSymbolRequest(
                query,
                max_results,
                exact,
                include_content=include_content,
                response_format=response_format,
            )
        )

    def find_definition(
        self, query: str, *, max_results: int = 20
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_definition.execute(FindDefinitionRequest(query, max_results))

    def find_references(
        self,
        query: str,
        *,
        max_results: int = 100,
        include_comments: bool = True,
    ) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return self._container.find_references.execute(
            FindReferencesRequest(
                query,
                max_results,
                include_comments=include_comments,
            )
        )

    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 10_000,
        cursor: str | None = None,
        sort: str = "path",
        sort_direction: str = "asc",
        include_total_count: bool = True,
    ) -> ToolResult[FileListingPage]:
        return self._container.list_files.execute(
            ListFilesRequest(
                include_globs,
                exclude_globs,
                max_results,
                cursor,
                sort,
                sort_direction,
                include_total_count,
            )
        )

    def search_files(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        case_sensitive: bool = False,
    ) -> ToolResult[tuple[FileMatch, ...]]:
        return self._container.search_files.execute(
            SearchFilesRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                case_sensitive,
            )
        )

    def search_text(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.search_text.execute(
            SearchTextRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
        )

    def search_regex(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 0,
        case_sensitive: bool = False,
        timeout_seconds: float = 10.0,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.search_regex.execute(
            SearchRegexRequest(
                query,
                include_globs,
                exclude_globs,
                max_results,
                context_lines,
                case_sensitive,
                timeout_seconds,
            )
        )

    def semantic_search(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_results: int = 50,
    ) -> ToolResult[tuple[SearchHit, ...]]:
        return self._container.semantic_search.execute(
            SemanticSearchRequest(
                query,
                include_globs,
                exclude_globs,
                languages,
                max_results,
            )
        )

    def search_code(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_results: int = 50,
        context_lines: int = 2,
        timeout_seconds: float = 10.0,
        snippet_mode: str = "match_window",
        max_snippet_lines: int = 40,
        max_snippet_chars: int = 6_000,
    ) -> ToolResult[tuple[HybridSearchHit, ...]]:
        return self._container.search_code.execute(
            SearchCodeRequest(
                query=query,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                languages=languages,
                max_results=max_results,
                context_lines=context_lines,
                timeout_seconds=timeout_seconds,
                snippet_mode=snippet_mode,
                max_snippet_lines=max_snippet_lines,
                max_snippet_chars=max_snippet_chars,
            )
        )

    def build_context(
        self,
        query: str,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_tokens: int = 12_000,
        reserved_tokens: int = 0,
        max_files: int = 12,
        max_snippets: int = 20,
        max_expansion_depth: int = 2,
    ) -> ToolResult[ContextBundle]:
        return self._container.build_context.execute(
            BuildContextRequest(
                query,
                include_globs,
                exclude_globs,
                languages,
                max_tokens,
                reserved_tokens,
                max_files,
                max_snippets,
                max_expansion_depth,
            )
        )

    def get_repository_map(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        max_files: int = 200,
        max_symbols_per_file: int = 10,
        mode: str = "detailed",
        path: str | None = None,
        max_depth: int | None = None,
        include_symbols: bool | None = None,
    ) -> ToolResult[RepositoryMap]:
        return self._container.get_repository_map.execute(
            GetRepositoryMapRequest(
                include_globs,
                exclude_globs,
                languages,
                max_files,
                max_symbols_per_file,
                mode=mode,
                path=path,
                max_depth=max_depth,
                include_symbols=include_symbols,
            )
        )

    def read_file(
        self,
        path: str,
        *,
        max_chars: int = 200_000,
        max_lines: int = 5_000,
        include_line_numbers: bool = False,
    ) -> ToolResult[SourceRead]:
        return self._container.read_file.execute(
            ReadFileRequest(path, max_chars, max_lines, include_line_numbers)
        )

    def read_range(
        self,
        path: str,
        start_line: int,
        end_line: int,
        *,
        max_chars: int = 200_000,
        include_line_numbers: bool = False,
    ) -> ToolResult[SourceRead]:
        return self._container.read_range.execute(
            ReadRangeRequest(path, start_line, end_line, max_chars, include_line_numbers)
        )
