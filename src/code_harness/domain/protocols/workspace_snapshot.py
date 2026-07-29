from typing import Protocol

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import WorkspaceSnapshot


class WorkspaceSnapshotProvider(Protocol):
    def snapshot_for_change_set(self, change_set: ChangeSet) -> WorkspaceSnapshot: ...
