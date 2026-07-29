from dataclasses import dataclass

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.execution import ExecutionResult


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    digest: str
    change_set_id: str
    base_sha: str | None
    file_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ValidationRunResult:
    change_set_id: str
    workspace_snapshot_digest: str
    execution: ExecutionResult
    used_worktree: bool = False


@dataclass(frozen=True, slots=True)
class ReviewFixApplication:
    base_change_set_id: str
    workspace_snapshot_digest: str
    applied_paths: tuple[str, ...]
    patch_sha256: str
    new_change_set: ChangeSet


@dataclass(frozen=True, slots=True)
class ReviewComment:
    path: str
    body: str
    start_line: int | None = None
    end_line: int | None = None
    side: str = "RIGHT"


@dataclass(frozen=True, slots=True)
class PublishedReview:
    publication_id: str
    path: str
    change_set_id: str
    repository_id: str
    head_sha: str | None
    comments: tuple[ReviewComment, ...]


@dataclass(frozen=True, slots=True)
class ReviewCommitResult:
    commit_sha: str
    change_set_id: str
    message: str
    workspace_snapshot_digest: str
