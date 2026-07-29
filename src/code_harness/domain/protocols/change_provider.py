from typing import Protocol

from code_harness.domain.models.change_set import ChangeDiff, ChangeSet, ChangeSetRequest


class ChangeProvider(Protocol):
    def create_change_set(self, request: ChangeSetRequest) -> ChangeSet: ...

    def get_change_set(self, change_set_id: str) -> ChangeSet: ...

    def read_diff(self, change_set: ChangeSet) -> ChangeDiff: ...
