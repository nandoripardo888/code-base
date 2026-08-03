"""Application service for grouped change reviews."""

from __future__ import annotations

from dataclasses import dataclass

from code_harness.errors import PatchHistoryError
from code_harness.history import HistoryManager, PatchGroupManifest, TransactionManifest
from code_harness.review.diff_builder import build_side_by_side
from code_harness.review.models import (
    PatchGroupList,
    PatchGroupListItem,
    PatchGroupSummary,
    PatchListItem,
    PatchSummary,
    ReviewFileDiff,
    ReviewFileSummary,
)

_VISIBLE_STATUSES = {"applied", "rolled_back"}
_LEGACY_PREFIX = "legacy-group-"


@dataclass(frozen=True, slots=True)
class ReviewSelection:
    group_id: str
    transaction_id: str


@dataclass(frozen=True, slots=True)
class _PatchGroup:
    group_id: str
    group_title: str
    created_at: str
    updated_at: str
    legacy: bool
    transactions: tuple[TransactionManifest, ...]


class ReviewService:
    def __init__(self, history: HistoryManager) -> None:
        self.history = history

    def resolve_selection(
        self,
        identifier: str = "latest",
        *,
        transaction_id: str | None = None,
    ) -> ReviewSelection:
        groups = self._groups()
        if not groups:
            raise PatchHistoryError("No retained reviews were found.")
        if identifier == "latest":
            group = groups[0]
        else:
            selected_group = next(
                (item for item in groups if item.group_id == identifier), None
            )
            if selected_group is None:
                transaction = self.history.load(identifier)
                group = self._group_for_transaction(transaction, groups)
            else:
                group = selected_group
        if transaction_id in {None, "latest"}:
            selected = group.transactions[-1]
        else:
            selected_transaction = next(
                (item for item in group.transactions if item.transaction_id == transaction_id),
                None,
            )
            if selected_transaction is None:
                raise PatchHistoryError(
                    f"Transaction {transaction_id} does not belong to group {group.group_id}."
                )
            selected = selected_transaction
        return ReviewSelection(group.group_id, selected.transaction_id)

    def resolve_transaction_id(self, transaction_id: str) -> str:
        return self.resolve_selection(transaction_id).transaction_id

    def list_groups(self, *, limit: int = 50) -> PatchGroupList:
        if limit < 1 or limit > 200:
            raise ValueError("Review list limit must be between 1 and 200.")
        groups = self._groups()
        return PatchGroupList(
            items=tuple(self._list_item(item) for item in groups[:limit]),
            total=len(groups),
            retained_limit=self.history.policy.keep_last_per_workspace,
        )

    def get_group(self, group_id: str) -> PatchGroupSummary:
        group = self._load_group(group_id)
        patches = tuple(
            self._patch_list_item(item, with_diff=True)
            for item in group.transactions
        )
        additions = sum(item.additions or 0 for item in patches)
        deletions = sum(item.deletions or 0 for item in patches)
        reviewed, pending, rolled_back = self._state_counts(group.transactions)
        return PatchGroupSummary(
            group_id=group.group_id,
            group_title=group.group_title,
            created_at=group.created_at,
            updated_at=group.updated_at,
            legacy=group.legacy,
            patches_count=len(patches),
            files_changed=len({file.path for patch in patches for file in patch.files}),
            additions=additions,
            deletions=deletions,
            reviewed_count=reviewed,
            pending_count=pending,
            rolled_back_count=rolled_back,
            patches=patches,
        )

    def get_patch(self, group_id: str, transaction_id: str) -> PatchSummary:
        group = self._load_group(group_id)
        manifest = self._transaction_in_group(group, transaction_id)
        files, additions, deletions = self._build_file_summaries(manifest)
        return PatchSummary(
            group_id=group_id,
            transaction_id=manifest.transaction_id,
            source_tool=manifest.source_tool,
            status=manifest.status,
            review_state=manifest.review_state,
            created_at=manifest.created_at,
            reviewed_at=manifest.reviewed_at,
            description=self._description(manifest),
            files_changed=len(manifest.files),
            additions=additions,
            deletions=deletions,
            files=files,
        )

    def get_summary(self, transaction_id: str) -> PatchSummary:
        selection = self.resolve_selection(transaction_id)
        return self.get_patch(selection.group_id, selection.transaction_id)

    def get_file(
        self,
        group_id: str,
        transaction_id: str,
        file_index: int,
        *,
        collapse_context: bool = True,
    ) -> ReviewFileDiff:
        group = self._load_group(group_id)
        manifest = self._transaction_in_group(group, transaction_id)
        if file_index < 0 or file_index >= len(manifest.files):
            self.history.read_before_content(transaction_id, file_index)
        item = manifest.files[file_index]
        built = build_side_by_side(
            self.history.read_before_content(transaction_id, file_index),
            self.history.read_after_content(transaction_id, file_index),
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

    def complete(self, group_id: str, transaction_id: str) -> PatchSummary:
        group = self._load_group(group_id)
        self._transaction_in_group(group, transaction_id)
        self.history.mark_reviewed(transaction_id)
        return self.get_patch(group_id, transaction_id)

    def rollback(self, group_id: str, transaction_id: str) -> PatchSummary:
        group = self._load_group(group_id)
        self._transaction_in_group(group, transaction_id)
        with self.history.exclusive():
            self.history.rollback(transaction_id, force=False)
        return self.get_patch(group_id, transaction_id)

    def rollback_group(self, group_id: str) -> PatchGroupSummary:
        group = self._load_group(group_id)
        if group.legacy:
            with self.history.exclusive():
                self.history.rollback(group.transactions[0].transaction_id, force=False)
        else:
            self.history.rollback_group(group_id)
        return self.get_group(group_id)

    def _groups(self) -> tuple[_PatchGroup, ...]:
        visible = tuple(
            item for item in self.history.list_transactions() if item.status in _VISIBLE_STATUSES
        )
        by_group: dict[str, list[TransactionManifest]] = {}
        legacy: list[_PatchGroup] = []
        for manifest in visible:
            if manifest.group_id is None:
                legacy.append(self._legacy_group(manifest))
            else:
                by_group.setdefault(manifest.group_id, []).append(manifest)
        groups: list[_PatchGroup] = []
        for stored_group in self.history.list_groups():
            transactions = by_group.get(stored_group.group_id)
            if transactions:
                groups.append(self._stored_group(stored_group, transactions))
        groups.extend(legacy)
        return tuple(sorted(groups, key=lambda item: item.updated_at, reverse=True))

    def _load_group(self, group_id: str) -> _PatchGroup:
        group = next((item for item in self._groups() if item.group_id == group_id), None)
        if group is None:
            raise PatchHistoryError(f"Patch group was not found: {group_id}")
        return group

    def _group_for_transaction(
        self,
        transaction: TransactionManifest,
        groups: tuple[_PatchGroup, ...],
    ) -> _PatchGroup:
        expected = transaction.group_id or f"{_LEGACY_PREFIX}{transaction.transaction_id}"
        group = next((item for item in groups if item.group_id == expected), None)
        if group is None:
            raise PatchHistoryError(
                f"Patch group was not found for transaction {transaction.transaction_id}."
            )
        return group

    def _list_item(self, group: _PatchGroup) -> PatchGroupListItem:
        patches = tuple(self._patch_list_item(item) for item in group.transactions)
        totals_known = all(
            item.additions is not None and item.deletions is not None for item in patches
        )
        reviewed, pending, rolled_back = self._state_counts(group.transactions)
        return PatchGroupListItem(
            group_id=group.group_id,
            group_title=group.group_title,
            created_at=group.created_at,
            updated_at=group.updated_at,
            legacy=group.legacy,
            patches_count=len(patches),
            files_changed=len({file.path for patch in patches for file in patch.files}),
            additions=sum(item.additions or 0 for item in patches) if totals_known else None,
            deletions=sum(item.deletions or 0 for item in patches) if totals_known else None,
            reviewed_count=reviewed,
            pending_count=pending,
            rolled_back_count=rolled_back,
            patches=patches,
        )

    def _patch_list_item(
        self,
        manifest: TransactionManifest,
        *,
        with_diff: bool = False,
    ) -> PatchListItem:
        additions: int | None
        deletions: int | None
        if with_diff:
            files, additions, deletions = self._build_file_summaries(manifest)
        else:
            additions = manifest.summary_additions
            deletions = manifest.summary_deletions
            files = tuple(
                ReviewFileSummary(
                    index=index,
                    path=item.path,
                    operation=item.operation,
                    additions=None,
                    deletions=None,
                    binary=None,
                )
                for index, item in enumerate(manifest.files)
            )
        return PatchListItem(
            transaction_id=manifest.transaction_id,
            source_tool=manifest.source_tool,
            status=manifest.status,
            review_state=manifest.review_state,
            created_at=manifest.created_at,
            reviewed_at=manifest.reviewed_at,
            description=self._description(manifest),
            files_changed=len(manifest.files),
            additions=additions,
            deletions=deletions,
            files=files,
        )

    def _build_file_summaries(
        self,
        manifest: TransactionManifest,
    ) -> tuple[tuple[ReviewFileSummary, ...], int, int]:
        summaries: list[ReviewFileSummary] = []
        additions = 0
        deletions = 0
        for index, item in enumerate(manifest.files):
            built = build_side_by_side(
                self.history.read_before_content(manifest.transaction_id, index),
                self.history.read_after_content(manifest.transaction_id, index),
            )
            additions += built.additions
            deletions += built.deletions
            summaries.append(
                ReviewFileSummary(
                    index=index,
                    path=item.path,
                    operation=item.operation,
                    additions=built.additions,
                    deletions=built.deletions,
                    binary=built.binary,
                )
            )
        if manifest.summary_additions != additions or manifest.summary_deletions != deletions:
            self.history.set_review_summary(
                manifest.transaction_id,
                additions=additions,
                deletions=deletions,
            )
        return tuple(summaries), additions, deletions

    @staticmethod
    def _transaction_in_group(
        group: _PatchGroup,
        transaction_id: str,
    ) -> TransactionManifest:
        manifest = next(
            (item for item in group.transactions if item.transaction_id == transaction_id),
            None,
        )
        if manifest is None:
            raise PatchHistoryError(
                f"Transaction {transaction_id} does not belong to group {group.group_id}."
            )
        return manifest

    @staticmethod
    def _description(manifest: TransactionManifest) -> str:
        return manifest.description or f"Atualização via {manifest.source_tool}"

    @staticmethod
    def _state_counts(
        transactions: tuple[TransactionManifest, ...],
    ) -> tuple[int, int, int]:
        rolled_back = sum(item.status == "rolled_back" for item in transactions)
        reviewed = sum(
            item.status == "applied" and item.review_state == "reviewed"
            for item in transactions
        )
        pending = sum(
            item.status == "applied" and item.review_state != "reviewed"
            for item in transactions
        )
        return reviewed, pending, rolled_back

    @staticmethod
    def _stored_group(
        group: PatchGroupManifest,
        transactions: list[TransactionManifest],
    ) -> _PatchGroup:
        ordered = tuple(sorted(transactions, key=lambda item: item.created_at))
        return _PatchGroup(
            group_id=group.group_id,
            group_title=group.group_title,
            created_at=group.created_at,
            updated_at=max(group.updated_at, ordered[-1].updated_at),
            legacy=False,
            transactions=ordered,
        )

    @classmethod
    def _legacy_group(cls, manifest: TransactionManifest) -> _PatchGroup:
        return _PatchGroup(
            group_id=f"{_LEGACY_PREFIX}{manifest.transaction_id}",
            group_title=manifest.description or f"Revisão de {manifest.created_at}",
            created_at=manifest.created_at,
            updated_at=manifest.updated_at,
            legacy=True,
            transactions=(manifest,),
        )
