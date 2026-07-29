from typing import Protocol

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import ReviewFixApplication


class PatchApplier(Protocol):
    def apply_unified_patch(
        self,
        *,
        change_set: ChangeSet,
        patch_text: str,
        expected_file_hashes: tuple[tuple[str, str], ...],
    ) -> ReviewFixApplication: ...
