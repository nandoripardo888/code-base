"""Application service for reading and acting on patch reviews."""

from __future__ import annotations

from code_harness.history import HistoryManager, TransactionManifest
from code_harness.review.diff_builder import build_side_by_side
from code_harness.review.models import ReviewFileDiff, ReviewFileSummary, ReviewSummary


class ReviewService:
    def __init__(self, history: HistoryManager) -> None:
        self.history = history

    def resolve_transaction_id(self, transaction_id: str) -> str:
        if transaction_id == "latest":
            return self.history.latest_transaction(status="applied").transaction_id
        return self.history.load(transaction_id).transaction_id

    def get_summary(self, transaction_id: str) -> ReviewSummary:
        manifest = self.history.load(self.resolve_transaction_id(transaction_id))
        file_summaries: list[ReviewFileSummary] = []
        additions = 0
        deletions = 0
        for index, item in enumerate(manifest.files):
            built = build_side_by_side(
                self.history.read_before_content(manifest.transaction_id, index),
                self.history.read_after_content(manifest.transaction_id, index),
            )
            additions += built.additions
            deletions += built.deletions
            file_summaries.append(
                ReviewFileSummary(
                    path=item.path,
                    operation=item.operation,
                    additions=built.additions,
                    deletions=built.deletions,
                    binary=built.binary,
                )
            )
        if manifest.summary_additions != additions or manifest.summary_deletions != deletions:
            manifest = self.history.set_review_summary(
                manifest.transaction_id,
                additions=additions,
                deletions=deletions,
            )
        return self._summary_from_manifest(
            manifest,
            tuple(file_summaries),
            additions,
            deletions,
        )

    def get_file(
        self,
        transaction_id: str,
        file_index: int,
        *,
        collapse_context: bool = True,
    ) -> ReviewFileDiff:
        resolved = self.resolve_transaction_id(transaction_id)
        manifest = self.history.load(resolved)
        if file_index < 0 or file_index >= len(manifest.files):
            # Let HistoryManager provide the stable typed error.
            self.history.read_before_content(resolved, file_index)
        item = manifest.files[file_index]
        built = build_side_by_side(
            self.history.read_before_content(resolved, file_index),
            self.history.read_after_content(resolved, file_index),
            collapse_context=collapse_context,
        )
        return ReviewFileDiff(
            index=file_index,
            path=item.path,
            operation=item.operation,
            additions=built.additions,
            deletions=built.deletions,
            binary=built.binary,
            rows=built.rows,
        )

    def complete(self, transaction_id: str) -> ReviewSummary:
        resolved = self.resolve_transaction_id(transaction_id)
        self.history.mark_reviewed(resolved)
        return self.get_summary(resolved)

    def rollback(self, transaction_id: str) -> ReviewSummary:
        resolved = self.resolve_transaction_id(transaction_id)
        with self.history.exclusive():
            self.history.rollback(resolved, force=False)
        return self.get_summary(resolved)

    @staticmethod
    def _summary_from_manifest(
        manifest: TransactionManifest,
        files: tuple[ReviewFileSummary, ...],
        additions: int,
        deletions: int,
    ) -> ReviewSummary:
        return ReviewSummary(
            transaction_id=manifest.transaction_id,
            source_tool=manifest.source_tool,
            status=manifest.status,
            review_state=manifest.review_state,
            created_at=manifest.created_at,
            reviewed_at=manifest.reviewed_at,
            files_changed=len(manifest.files),
            additions=additions,
            deletions=deletions,
            files=files,
        )
