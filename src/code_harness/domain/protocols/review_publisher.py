from typing import Protocol

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import PublishedReview, ReviewComment


class ReviewPublisher(Protocol):
    def publish(
        self,
        *,
        change_set: ChangeSet,
        title: str,
        body: str,
        comments: tuple[ReviewComment, ...],
    ) -> PublishedReview: ...
