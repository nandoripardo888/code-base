from typing import Protocol

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import ReviewCommitResult


class ReviewCommitter(Protocol):
    def create_commit(
        self,
        *,
        change_set: ChangeSet,
        message: str,
        workspace_snapshot_digest: str,
        paths: tuple[str, ...] | None = None,
    ) -> ReviewCommitResult: ...
