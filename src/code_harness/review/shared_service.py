"""Shared review routing across multiple isolated workspace services."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from code_harness.errors import PatchHistoryError
from code_harness.review.models import (
    PatchGroupList,
    PatchGroupSummary,
    PatchSummary,
    ReviewFileDiff,
)
from code_harness.review.service import ReviewSelection, ReviewService


class SharedReviewService:
    """Route review operations to exactly one registered workspace service."""

    def __init__(self) -> None:
        self._services: dict[str, ReviewService] = {}
        self._lock = threading.RLock()

    def register(self, service: ReviewService) -> None:
        workspace_id = service.history.workspace_id
        with self._lock:
            existing = self._services.get(workspace_id)
            if existing is not None and existing is not service:
                raise PatchHistoryError(
                    f"Review workspace is already registered: {workspace_id}."
                )
            self._services[workspace_id] = service

    def unregister(self, service: ReviewService) -> None:
        workspace_id = service.history.workspace_id
        with self._lock:
            if self._services.get(workspace_id) is service:
                self._services.pop(workspace_id, None)

    def clear(self) -> None:
        with self._lock:
            self._services.clear()

    def resolve_selection(
        self,
        identifier: str = "latest",
        *,
        transaction_id: str | None = None,
    ) -> ReviewSelection:
        if identifier == "latest":
            listing = self.list_groups(limit=200)
            if not listing.items:
                raise PatchHistoryError("No retained reviews were found.")
            identifier = listing.items[0].group_id
        service = self._service_for_identifier(identifier)
        return service.resolve_selection(identifier, transaction_id=transaction_id)

    def resolve_transaction_id(self, transaction_id: str) -> str:
        service = self._service_for_identifier(transaction_id)
        return service.resolve_transaction_id(transaction_id)

    def list_groups(self, *, limit: int = 50) -> PatchGroupList:
        if limit < 1 or limit > 200:
            raise ValueError("Review list limit must be between 1 and 200.")
        services = self._snapshot()
        items = [item for service in services for item in service.list_groups(limit=200).items]
        duplicate_ids = self._duplicate_group_ids(items)
        if duplicate_ids:
            rendered = ", ".join(sorted(duplicate_ids))
            raise PatchHistoryError(
                f"Review group identifiers are ambiguous across workspaces: {rendered}."
            )
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return PatchGroupList(
            items=tuple(items[:limit]),
            total=len(items),
            retained_limit=sum(
                service.history.policy.keep_last_per_workspace for service in services
            ),
        )

    def get_group(self, group_id: str) -> PatchGroupSummary:
        return self._service_for_identifier(group_id).get_group(group_id)

    def get_patch(self, group_id: str, transaction_id: str) -> PatchSummary:
        return self._service_for_identifier(group_id).get_patch(group_id, transaction_id)

    def get_summary(self, transaction_id: str) -> PatchSummary:
        return self._service_for_identifier(transaction_id).get_summary(transaction_id)

    def get_file(
        self,
        group_id: str,
        transaction_id: str,
        file_index: int,
        *,
        collapse_context: bool = True,
    ) -> ReviewFileDiff:
        return self._service_for_identifier(group_id).get_file(
            group_id,
            transaction_id,
            file_index,
            collapse_context=collapse_context,
        )

    def complete(self, group_id: str, transaction_id: str) -> PatchSummary:
        return self._service_for_identifier(group_id).complete(group_id, transaction_id)

    def complete_group(self, group_id: str) -> PatchGroupSummary:
        return self._service_for_identifier(group_id).complete_group(group_id)

    def rollback(self, group_id: str, transaction_id: str) -> PatchSummary:
        return self._service_for_identifier(group_id).rollback(group_id, transaction_id)

    def rollback_group(self, group_id: str) -> PatchGroupSummary:
        return self._service_for_identifier(group_id).rollback_group(group_id)

    def _service_for_identifier(self, identifier: str) -> ReviewService:
        matches: list[ReviewService] = []
        for service in self._snapshot():
            try:
                service.resolve_selection(identifier)
            except PatchHistoryError:
                continue
            matches.append(service)
        if not matches:
            raise PatchHistoryError(f"Review identifier was not found: {identifier}")
        if len(matches) > 1:
            raise PatchHistoryError(
                f"Review identifier is ambiguous across workspaces: {identifier}"
            )
        return matches[0]

    def _snapshot(self) -> tuple[ReviewService, ...]:
        with self._lock:
            return tuple(self._services.values())

    @staticmethod
    def _duplicate_group_ids(items: Sequence[object]) -> set[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in items:
            group_id = getattr(item, "group_id", None)
            if not isinstance(group_id, str):
                continue
            if group_id in seen:
                duplicates.add(group_id)
            seen.add(group_id)
        return duplicates
