"""Persistent, project-independent change history and startup maintenance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from code_harness.encoding import atomic_write_bytes
from code_harness.errors import PatchHistoryError, PatchRollbackConflictError

_ACTIVE_STATUSES = {"prepared", "ready", "applying", "recovery_required"}
_TERMINAL_STATUSES = {
    "applied",
    "rolled_back",
    "failed",
    "failed_and_restored",
    "not_applied",
}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def default_history_root() -> Path:
    override = os.environ.get("CODE_HARNESS_HISTORY_DIR")
    if override:
        return Path(override).expanduser().resolve(strict=False)
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "code-harness" / "history"
    if xdg_state_home := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg_state_home) / "code-harness" / "history"
    return Path.home() / ".local" / "state" / "code-harness" / "history"


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    retention_days: int = 30
    keep_last_per_workspace: int = 20
    max_workspace_bytes: int = 250 * 1024 * 1024
    max_global_bytes: int = 1024 * 1024 * 1024
    stale_temp_hours: int = 24

    @classmethod
    def from_environment(cls) -> HistoryPolicy:
        return cls(
            retention_days=_env_int("CODE_HARNESS_HISTORY_RETENTION_DAYS", 30),
            keep_last_per_workspace=_env_int("CODE_HARNESS_HISTORY_KEEP_LAST", 20, minimum=1),
            max_workspace_bytes=_env_int("CODE_HARNESS_HISTORY_MAX_WORKSPACE_MB", 250)
            * 1024
            * 1024,
            max_global_bytes=_env_int("CODE_HARNESS_HISTORY_MAX_GLOBAL_MB", 1024) * 1024 * 1024,
            stale_temp_hours=_env_int("CODE_HARNESS_HISTORY_STALE_TEMP_HOURS", 24),
        )


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    operation: str
    existed_before: bool
    exists_after: bool | None
    before_sha256: str | None
    after_sha256: str | None
    before_object: str | None
    after_object: str | None
    encoding: str | None = None
    has_bom: bool = False
    line_ending: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "existed_before": self.existed_before,
            "exists_after": self.exists_after,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "before_object": self.before_object,
            "after_object": self.after_object,
            "encoding": self.encoding,
            "has_bom": self.has_bom,
            "line_ending": self.line_ending,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FileSnapshot:
        return cls(
            path=str(value["path"]),
            operation=str(value["operation"]),
            existed_before=bool(value["existed_before"]),
            exists_after=(
                None if value.get("exists_after") is None else bool(value["exists_after"])
            ),
            before_sha256=_optional_string(value.get("before_sha256")),
            after_sha256=_optional_string(value.get("after_sha256")),
            before_object=_optional_string(value.get("before_object")),
            after_object=_optional_string(value.get("after_object")),
            encoding=_optional_string(value.get("encoding")),
            has_bom=bool(value.get("has_bom", False)),
            line_ending=_optional_string(value.get("line_ending")),
        )


@dataclass(frozen=True, slots=True)
class PatchGroupManifest:
    group_id: str
    workspace_id: str
    workspace_root: str
    group_title: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "group_title": self.group_title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PatchGroupManifest:
        group_id = str(value.get("group_id") or value["review_id"])
        return cls(
            group_id=group_id,
            workspace_id=str(value["workspace_id"]),
            workspace_root=str(value["workspace_root"]),
            group_title=str(value.get("group_title") or value["title"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class TransactionManifest:
    transaction_id: str
    workspace_id: str
    workspace_root: str
    status: str
    created_at: str
    updated_at: str
    files: tuple[FileSnapshot, ...]
    patch_sha256: str | None
    git_version: str | None = None
    error: str | None = None
    rolled_back_at: str | None = None
    source_tool: str = "apply_patch"
    description: str | None = None
    review_state: str = "unreviewed"
    reviewed_at: str | None = None
    summary_additions: int | None = None
    summary_deletions: int | None = None
    group_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "patch_sha256": self.patch_sha256,
            "git_version": self.git_version,
            "error": self.error,
            "rolled_back_at": self.rolled_back_at,
            "source_tool": self.source_tool,
            "description": self.description,
            "review_state": self.review_state,
            "reviewed_at": self.reviewed_at,
            "summary_additions": self.summary_additions,
            "summary_deletions": self.summary_deletions,
            "group_id": self.group_id,
            "files": [item.to_dict() for item in self.files],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TransactionManifest:
        raw_files = value.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("manifest files must be a list")
        return cls(
            transaction_id=str(value["transaction_id"]),
            workspace_id=str(value["workspace_id"]),
            workspace_root=str(value["workspace_root"]),
            status=str(value["status"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            patch_sha256=_optional_string(value.get("patch_sha256")),
            git_version=_optional_string(value.get("git_version")),
            error=_optional_string(value.get("error")),
            rolled_back_at=_optional_string(value.get("rolled_back_at")),
            source_tool=str(value.get("source_tool", "apply_patch")),
            description=_optional_string(value.get("description")),
            review_state=str(value.get("review_state", "unreviewed")),
            reviewed_at=_optional_string(value.get("reviewed_at")),
            summary_additions=_optional_int(value.get("summary_additions")),
            summary_deletions=_optional_int(value.get("summary_deletions")),
            group_id=_optional_string(value.get("group_id") or value.get("review_id")),
            files=tuple(
                FileSnapshot.from_dict(item) for item in raw_files if isinstance(item, dict)
            ),
        )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, str, bytes, bytearray)):
        return int(value)
    raise TypeError("manifest summary value must be an integer")


class HistoryManager:
    """Stores byte-exact snapshots and maintains them without MCP-facing tools."""

    def __init__(
        self,
        project_root: Path,
        *,
        history_root: Path | None = None,
        policy: HistoryPolicy | None = None,
    ) -> None:
        self.project_root = project_root.resolve(strict=False)
        self.root = (history_root or default_history_root()).resolve(strict=False)
        root_identity = str(self.project_root)
        if os.name == "nt":
            root_identity = root_identity.casefold()
        self.workspace_id = hashlib.sha256(root_identity.encode("utf-8")).hexdigest()[:24]
        self.workspace_dir = self.root / self.workspace_id
        self.objects_dir = self.workspace_dir / "objects"
        self.transactions_dir = self.workspace_dir / "transactions"
        self.groups_dir = self.workspace_dir / "groups"
        self.legacy_reviews_dir = self.workspace_dir / "reviews"
        self.temporary_dir = self.workspace_dir / "tmp"
        self.policy = policy or HistoryPolicy.from_environment()
        self._mutation_lock = threading.RLock()
        self._initialize()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Serialize patch application and rollback for this workspace."""
        with self._mutation_lock:
            yield

    def _initialize(self) -> None:
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        self.groups_dir.mkdir(parents=True, exist_ok=True)
        self.temporary_dir.mkdir(parents=True, exist_ok=True)

    def maintain(self) -> dict[str, int]:
        """Recover interrupted work, apply retention, and remove orphan objects."""
        recovered = self._recover_group_rollbacks() + self._recover_interrupted()
        removed_transactions = self._cleanup_transactions()
        self._remove_orphan_groups()
        removed_objects = self._remove_orphan_objects()
        removed_temporary = self._cleanup_stale_temporary()
        removed_global = self._enforce_global_limit()
        return {
            "recovered": recovered,
            "removed_transactions": removed_transactions + removed_global,
            "removed_objects": removed_objects,
            "removed_temporary": removed_temporary,
        }

    def make_temporary_workspace(self, transaction_hint: str | None = None) -> Path:
        suffix = transaction_hint or uuid.uuid4().hex[:12]
        target = self.temporary_dir / f"patch-{suffix}"
        target.mkdir(parents=True, exist_ok=False)
        return target

    def store_object(self, content: bytes) -> str:
        object_id = _sha256(content)
        target = self._object_path(object_id)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, content)
        return object_id

    def read_object(self, object_id: str) -> bytes:
        target = self._object_path(object_id)
        if not target.is_file():
            raise PatchHistoryError(f"History object is missing: {object_id}")
        return target.read_bytes()

    def begin(
        self,
        patch_text: str | None,
        files: tuple[FileSnapshot, ...],
        *,
        git_version: str | None,
        source_tool: str = "apply_patch",
        description: str | None = None,
        group_id: str | None = None,
    ) -> TransactionManifest:
        now = datetime.now(UTC)
        transaction_id = f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        manifest = TransactionManifest(
            transaction_id=transaction_id,
            workspace_id=self.workspace_id,
            workspace_root=str(self.project_root),
            status="prepared",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            files=files,
            patch_sha256=(
                _sha256(patch_text.encode("utf-8")) if patch_text is not None else None
            ),
            git_version=git_version,
            source_tool=source_tool,
            description=description,
            group_id=group_id,
        )
        transaction_dir = self._transaction_dir(transaction_id)
        transaction_dir.mkdir(parents=True, exist_ok=False)
        if patch_text is not None:
            atomic_write_bytes(transaction_dir / "forward.patch", patch_text.encode("utf-8"))
        self.save(manifest)
        return manifest

    def create_group(self, group_title: str) -> PatchGroupManifest:
        now = datetime.now(UTC)
        group = PatchGroupManifest(
            group_id=f"group-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}",
            workspace_id=self.workspace_id,
            workspace_root=str(self.project_root),
            group_title=group_title,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        directory = self._group_dir(group.group_id)
        directory.mkdir(parents=True, exist_ok=False)
        self.save_group(group)
        return group

    def save_group(self, group: PatchGroupManifest) -> None:
        target = self._group_dir(group.group_id) / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(group.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write_bytes(target, rendered)

    def load_group(self, group_id: str) -> PatchGroupManifest:
        self._validate_identifier(group_id, "group")
        target = self._group_manifest_path(group_id)
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PatchHistoryError(f"Patch group was not found: {group_id}") from error
        if not isinstance(value, dict):
            raise PatchHistoryError(f"Patch group is invalid: {group_id}")
        group = PatchGroupManifest.from_dict(value)
        if group.workspace_id != self.workspace_id:
            raise PatchHistoryError(f"Patch group belongs to another workspace: {group_id}")
        return group

    def list_groups(self) -> tuple[PatchGroupManifest, ...]:
        groups = _read_group_manifests(self.groups_dir)
        legacy = _read_group_manifests(self.legacy_reviews_dir)
        by_id = {item.group_id: item for item in legacy}
        by_id.update({item.group_id: item for item in groups})
        return tuple(sorted(by_id.values(), key=lambda item: item.updated_at, reverse=True))

    def touch_group(self, group_id: str) -> PatchGroupManifest:
        group = self.load_group(group_id)
        updated = replace(group, updated_at=_utc_now())
        self.save_group(updated)
        return updated

    def transactions_for_group(self, group_id: str) -> tuple[TransactionManifest, ...]:
        return tuple(
            sorted(
                (item for item in self._list_manifests() if item.group_id == group_id),
                key=lambda item: item.created_at,
            )
        )

    def save(self, manifest: TransactionManifest) -> None:
        target = self._transaction_dir(manifest.transaction_id) / "manifest.json"
        rendered = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write_bytes(target, rendered)

    def update(
        self,
        manifest: TransactionManifest,
        *,
        status: str | None = None,
        files: tuple[FileSnapshot, ...] | None = None,
        error: str | None = None,
        rolled_back_at: str | None = None,
        review_state: str | None = None,
        reviewed_at: str | None = None,
        summary_additions: int | None = None,
        summary_deletions: int | None = None,
    ) -> TransactionManifest:
        updated = replace(
            manifest,
            status=status or manifest.status,
            files=files or manifest.files,
            error=error,
            rolled_back_at=rolled_back_at,
            review_state=review_state or manifest.review_state,
            reviewed_at=(
                reviewed_at if review_state is not None else manifest.reviewed_at
            ),
            summary_additions=(
                manifest.summary_additions
                if summary_additions is None
                else summary_additions
            ),
            summary_deletions=(
                manifest.summary_deletions
                if summary_deletions is None
                else summary_deletions
            ),
            updated_at=_utc_now(),
        )
        self.save(updated)
        return updated

    def load(self, transaction_id: str) -> TransactionManifest:
        self._validate_identifier(transaction_id, "transaction")
        target = self._transaction_dir(transaction_id) / "manifest.json"
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PatchHistoryError(f"Patch transaction was not found: {transaction_id}") from error
        if not isinstance(value, dict):
            raise PatchHistoryError(f"Patch transaction is invalid: {transaction_id}")
        return TransactionManifest.from_dict(value)

    def list_transactions(self, *, status: str | None = None) -> tuple[TransactionManifest, ...]:
        """Return this workspace's transactions, newest first."""
        manifests = self._list_manifests()
        if status is not None:
            manifests = [item for item in manifests if item.status == status]
        return tuple(sorted(manifests, key=lambda item: item.created_at, reverse=True))

    def latest_transaction(self, *, status: str | None = None) -> TransactionManifest:
        """Return the newest transaction, optionally limited to one operational status."""
        transactions = self.list_transactions(status=status)
        if not transactions:
            detail = f" with status {status!r}" if status else ""
            raise PatchHistoryError(f"No patch transactions were found{detail}.")
        return transactions[0]

    def mark_reviewed(self, transaction_id: str) -> TransactionManifest:
        """Mark a transaction reviewed without changing its rollback eligibility."""
        with self._mutation_lock:
            manifest = self.load(transaction_id)
            return self.update(
                manifest,
                review_state="reviewed",
                reviewed_at=_utc_now(),
            )

    def set_review_summary(
        self,
        transaction_id: str,
        *,
        additions: int,
        deletions: int,
    ) -> TransactionManifest:
        with self._mutation_lock:
            manifest = self.load(transaction_id)
            if (
                manifest.summary_additions == additions
                and manifest.summary_deletions == deletions
            ):
                return manifest
            updated = replace(
                manifest,
                summary_additions=additions,
                summary_deletions=deletions,
            )
            self.save(updated)
            return updated

    def read_before_content(self, transaction_id: str, file_index: int) -> bytes | None:
        """Read a before snapshot by manifest index without exposing object identifiers."""
        manifest = self.load(transaction_id)
        item = self._file_at(manifest, file_index)
        if not item.existed_before:
            return None
        if item.before_object is None:
            raise PatchHistoryError(f"Before snapshot is missing for {item.path}.")
        return self.read_object(item.before_object)

    def read_after_content(self, transaction_id: str, file_index: int) -> bytes | None:
        """Read an after snapshot by manifest index without reading the live project file."""
        manifest = self.load(transaction_id)
        item = self._file_at(manifest, file_index)
        if not item.exists_after:
            return None
        if item.after_object is None:
            raise PatchHistoryError(f"After snapshot is missing for {item.path}.")
        return self.read_object(item.after_object)

    def rollback(self, transaction_id: str, *, force: bool = False) -> dict[str, object]:
        manifest = self.load(transaction_id)
        if manifest.status != "applied":
            raise PatchHistoryError(
                "Transaction "
                f"{transaction_id} cannot be rolled back from status {manifest.status!r}."
            )
        conflicts = self._current_conflicts(manifest.files, compare_after=True)
        if conflicts and not force:
            raise PatchRollbackConflictError(transaction_id, conflicts)
        try:
            self._restore_before(manifest.files)
        except OSError as error:
            raise PatchHistoryError(
                f"Could not roll back transaction {transaction_id}: {error}"
            ) from error
        self.update(manifest, status="rolled_back", rolled_back_at=_utc_now())
        return {
            "transaction_id": transaction_id,
            "status": "rolled_back",
            "files_restored": len(manifest.files),
            "forced": force,
        }

    def rollback_group(self, group_id: str) -> dict[str, object]:
        """Rollback every still-applied transaction after a complete reverse simulation."""
        with self._mutation_lock:
            group = self.load_group(group_id)
            transactions = tuple(
                item
                for item in reversed(self.transactions_for_group(group_id))
                if item.status == "applied"
            )
            if not transactions:
                raise PatchHistoryError(
                    f"Patch group {group_id} has no applied changes to roll back."
                )

            paths = sorted({item.path for manifest in transactions for item in manifest.files})
            initial = {path: self._read_project_state(path) for path in paths}
            simulated = dict(initial)
            conflicts: set[str] = set()
            for manifest in transactions:
                for item in manifest.files:
                    expected = self._snapshot_content(item, after=True)
                    if simulated[item.path] != expected:
                        conflicts.add(item.path)
                if conflicts:
                    continue
                for item in manifest.files:
                    simulated[item.path] = self._snapshot_content(item, after=False)
            if conflicts:
                raise PatchRollbackConflictError(group_id, sorted(conflicts))

            journal = {
                "group_id": group_id,
                "transaction_ids": [item.transaction_id for item in transactions],
                "initial": [self._journal_state(path, initial[path]) for path in paths],
            }
            journal_path = self._existing_group_dir(group_id) / "rollback-journal.json"
            atomic_write_bytes(
                journal_path,
                json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            try:
                for path in paths:
                    self._write_project_state(path, simulated[path])
            except OSError as error:
                restored = self._restore_state_map(initial)
                if restored:
                    journal_path.unlink(missing_ok=True)
                raise PatchHistoryError(
                    f"Could not roll back patch group {group_id}: {error}"
                ) from error

            rolled_back_at = _utc_now()
            for manifest in transactions:
                self.update(
                    manifest,
                    status="rolled_back",
                    rolled_back_at=rolled_back_at,
                )
            journal_path.unlink(missing_ok=True)
            self.touch_group(group.group_id)
            return {
                "group_id": group_id,
                "status": "rolled_back",
                "transactions_rolled_back": len(transactions),
                "files_restored": len(paths),
            }

    def _recover_group_rollbacks(self) -> int:
        recovered = 0
        roots = (self.groups_dir, self.legacy_reviews_dir)
        for root in roots:
            for journal_path in root.glob("*/rollback-journal.json"):
                recovered += self._recover_group_rollback(journal_path)
        return recovered

    def _recover_group_rollback(self, journal_path: Path) -> int:
        try:
            value = json.loads(journal_path.read_text(encoding="utf-8"))
            raw_initial = value["initial"]
            raw_ids = value["transaction_ids"]
            if not isinstance(raw_initial, list) or not isinstance(raw_ids, list):
                return 0
            states: dict[str, bytes | None] = {}
            for item in raw_initial:
                if not isinstance(item, dict):
                    raise ValueError("invalid rollback state")
                path = str(item["path"])
                object_id = _optional_string(item.get("object"))
                states[path] = self.read_object(object_id) if object_id else None
            if not self._restore_state_map(states):
                return 0
            for transaction_id in raw_ids:
                manifest = self.load(str(transaction_id))
                self.save(
                    replace(
                        manifest,
                        status="applied",
                        rolled_back_at=None,
                        error="Recovered interrupted group rollback.",
                        updated_at=_utc_now(),
                    )
                )
            journal_path.unlink(missing_ok=True)
            return 1
        except (OSError, ValueError, KeyError, TypeError, PatchHistoryError):
            return 0

    def _snapshot_content(self, item: FileSnapshot, *, after: bool) -> bytes | None:
        exists = item.exists_after if after else item.existed_before
        object_id = item.after_object if after else item.before_object
        if not exists:
            return None
        if object_id is None:
            raise PatchHistoryError(f"History snapshot is missing for {item.path}.")
        return self.read_object(object_id)

    def _read_project_state(self, relative: str) -> bytes | None:
        target = self._project_path(relative)
        if not target.is_file():
            return None
        try:
            return target.read_bytes()
        except OSError as error:
            raise PatchHistoryError(f"Could not read current file {relative}: {error}") from error

    def _write_project_state(self, relative: str, content: bytes | None) -> None:
        target = self._project_path(relative)
        if content is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write_bytes(target, content)

    def _restore_state_map(self, states: dict[str, bytes | None]) -> bool:
        try:
            for path, content in states.items():
                self._write_project_state(path, content)
        except OSError:
            return False
        return True

    def _journal_state(self, path: str, content: bytes | None) -> dict[str, object]:
        return {
            "path": path,
            "object": self.store_object(content) if content is not None else None,
        }

    def _recover_interrupted(self) -> int:
        recovered = 0
        for manifest in self._list_manifests():
            if manifest.status not in {"prepared", "ready", "applying"}:
                continue
            if manifest.status in {"prepared", "ready"}:
                self.update(
                    manifest, status="not_applied", error="Recovered before real-file commit."
                )
                recovered += 1
                continue
            after_conflicts = self._current_conflicts(manifest.files, compare_after=True)
            before_conflicts = self._current_conflicts(manifest.files, compare_after=False)
            if not after_conflicts:
                self.update(manifest, status="applied", error="Recovered completed commit.")
            elif not before_conflicts:
                self.update(
                    manifest, status="not_applied", error="Recovered before commit completed."
                )
            else:
                try:
                    self._restore_before(manifest.files)
                    self.update(
                        manifest,
                        status="failed_and_restored",
                        error="Recovered interrupted partial commit and restored byte snapshots.",
                    )
                except OSError as error:
                    self.update(manifest, status="recovery_required", error=str(error))
            recovered += 1
        return recovered

    def _current_conflicts(
        self,
        files: tuple[FileSnapshot, ...],
        *,
        compare_after: bool,
    ) -> list[str]:
        conflicts: list[str] = []
        for item in files:
            target = self._project_path(item.path)
            expected_exists = item.exists_after if compare_after else item.existed_before
            expected_hash = item.after_sha256 if compare_after else item.before_sha256
            if bool(expected_exists) != target.is_file():
                conflicts.append(item.path)
                continue
            if expected_exists and expected_hash is not None:
                try:
                    current_hash = _sha256(target.read_bytes())
                except OSError:
                    conflicts.append(item.path)
                    continue
                if current_hash != expected_hash:
                    conflicts.append(item.path)
        return conflicts

    def _restore_before(self, files: tuple[FileSnapshot, ...]) -> None:
        for item in files:
            target = self._project_path(item.path)
            if not item.existed_before:
                target.unlink(missing_ok=True)
                continue
            if item.before_object is None:
                raise OSError(f"Missing before snapshot for {item.path}")
            atomic_write_bytes(target, self.read_object(item.before_object))

    def _cleanup_transactions(self) -> int:
        groups = _history_groups(self.workspace_dir)
        keep_ids = {item.key for item in groups[: self.policy.keep_last_per_workspace]}
        cutoff = datetime.now(UTC) - timedelta(days=self.policy.retention_days)
        removed = 0
        for group in reversed(groups):
            if group.key in keep_ids or any(
                item.status in _ACTIVE_STATUSES for item in group.transactions
            ):
                continue
            if _parse_datetime(group.updated_at) >= cutoff:
                continue
            removed += _remove_history_group(group)
        self._remove_orphan_objects()
        while self._directory_size(self.workspace_dir) > self.policy.max_workspace_bytes:
            candidates = [
                item
                for item in reversed(_history_groups(self.workspace_dir))
                if item.key not in keep_ids
                and not any(tx.status in _ACTIVE_STATUSES for tx in item.transactions)
            ]
            if not candidates:
                break
            removed += _remove_history_group(candidates[0])
            self._remove_orphan_objects()
        return removed

    def _enforce_global_limit(self) -> int:
        if self._directory_size(self.root) <= self.policy.max_global_bytes:
            return 0
        candidates: list[_HistoryGroup] = []
        for workspace in self.root.iterdir() if self.root.exists() else ():
            groups = _history_groups(workspace)
            keep = {item.key for item in groups[: self.policy.keep_last_per_workspace]}
            candidates.extend(
                item
                for item in groups
                if item.key not in keep
                and not any(tx.status in _ACTIVE_STATUSES for tx in item.transactions)
            )
        removed = 0
        for group in sorted(candidates, key=lambda value: value.updated_at):
            if self._directory_size(self.root) <= self.policy.max_global_bytes:
                break
            removed += _remove_history_group(group)
        for workspace in self.root.iterdir() if self.root.exists() else ():
            self._remove_orphan_objects_for(workspace)
        return removed

    def _remove_orphan_groups(self) -> int:
        referenced = {
            item.group_id for item in self._list_manifests() if item.group_id is not None
        }
        removed = 0
        for group in self.list_groups():
            if group.group_id not in referenced:
                shutil.rmtree(self._group_dir(group.group_id), ignore_errors=True)
                shutil.rmtree(
                    self.legacy_reviews_dir / group.group_id,
                    ignore_errors=True,
                )
                removed += 1
        return removed

    def _remove_orphan_objects(self) -> int:
        return self._remove_orphan_objects_for(self.workspace_dir)

    def _remove_orphan_objects_for(self, workspace_dir: Path) -> int:
        referenced: set[str] = set()
        for manifest in _read_manifests(workspace_dir / "transactions"):
            for item in manifest.files:
                if item.before_object:
                    referenced.add(item.before_object)
                if item.after_object:
                    referenced.add(item.after_object)
        removed = 0
        objects_dir = workspace_dir / "objects"
        if not objects_dir.exists():
            return 0
        for path in objects_dir.rglob("*"):
            if path.is_file() and path.name not in referenced:
                path.unlink(missing_ok=True)
                removed += 1
        for directory in sorted(
            (path for path in objects_dir.rglob("*") if path.is_dir()), reverse=True
        ):
            with suppress(OSError):
                directory.rmdir()
        return removed

    def _cleanup_stale_temporary(self) -> int:
        cutoff = time.time() - self.policy.stale_temp_hours * 3600
        removed = 0
        for directory in self.temporary_dir.iterdir() if self.temporary_dir.exists() else ():
            try:
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def _list_manifests(self) -> list[TransactionManifest]:
        return _read_manifests(self.transactions_dir)

    def _project_path(self, relative: str) -> Path:
        candidate = (self.project_root / Path(relative)).resolve(strict=False)
        try:
            candidate.relative_to(self.project_root)
        except ValueError as error:
            raise PatchHistoryError(f"History path escapes the project root: {relative}") from error
        return candidate

    def _object_path(self, object_id: str) -> Path:
        if len(object_id) != 64 or any(char not in "0123456789abcdef" for char in object_id):
            raise PatchHistoryError("Invalid history object id.")
        return self.objects_dir / object_id[:2] / object_id

    def _transaction_dir(self, transaction_id: str) -> Path:
        return self.transactions_dir / transaction_id

    def _group_dir(self, group_id: str) -> Path:
        return self.groups_dir / group_id

    def _existing_group_dir(self, group_id: str) -> Path:
        current = self._group_dir(group_id)
        return current if current.exists() else self.legacy_reviews_dir / group_id

    def _group_manifest_path(self, group_id: str) -> Path:
        return self._existing_group_dir(group_id) / "manifest.json"

    @staticmethod
    def _validate_identifier(value: str, kind: str) -> None:
        if not value or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in value
        ):
            raise PatchHistoryError(f"Invalid {kind} id.")

    @staticmethod
    def _file_at(manifest: TransactionManifest, file_index: int) -> FileSnapshot:
        if file_index < 0 or file_index >= len(manifest.files):
            raise PatchHistoryError(f"Patch group file index is out of range: {file_index}")
        return manifest.files[file_index]

    @staticmethod
    def _directory_size(directory: Path) -> int:
        total = 0
        if not directory.exists():
            return 0
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def _read_manifests(transactions_dir: Path) -> list[TransactionManifest]:
    manifests: list[TransactionManifest] = []
    if not transactions_dir.exists():
        return manifests
    for target in transactions_dir.glob("*/manifest.json"):
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                manifests.append(TransactionManifest.from_dict(value))
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return manifests


def _read_group_manifests(groups_dir: Path) -> list[PatchGroupManifest]:
    manifests: list[PatchGroupManifest] = []
    if not groups_dir.exists():
        return manifests
    for target in groups_dir.glob("*/manifest.json"):
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                manifests.append(PatchGroupManifest.from_dict(value))
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return manifests


@dataclass(frozen=True, slots=True)
class _HistoryGroup:
    key: str
    updated_at: str
    transactions: tuple[TransactionManifest, ...]
    workspace_dir: Path
    group_dir: Path | None


def _history_groups(workspace_dir: Path) -> list[_HistoryGroup]:
    transactions = _read_manifests(workspace_dir / "transactions")
    legacy_groups = _read_group_manifests(workspace_dir / "reviews")
    groups = {item.group_id: item for item in legacy_groups}
    groups.update(
        {item.group_id: item for item in _read_group_manifests(workspace_dir / "groups")}
    )
    grouped: dict[str, list[TransactionManifest]] = {}
    for item in transactions:
        key = item.group_id or f"legacy-group-{item.transaction_id}"
        grouped.setdefault(key, []).append(item)
    result: list[_HistoryGroup] = []
    for key, items in grouped.items():
        group = groups.get(key)
        transaction_updated_at = max(item.updated_at for item in items)
        updated_at = (
            max(group.updated_at, transaction_updated_at)
            if group
            else transaction_updated_at
        )
        result.append(
            _HistoryGroup(
                key=key,
                updated_at=updated_at,
                transactions=tuple(items),
                workspace_dir=workspace_dir,
                group_dir=(
                    (workspace_dir / "groups" / key)
                    if (workspace_dir / "groups" / key).exists()
                    else (workspace_dir / "reviews" / key) if group else None
                ),
            )
        )
    return sorted(result, key=lambda item: item.updated_at, reverse=True)


def _remove_history_group(group: _HistoryGroup) -> int:
    for manifest in group.transactions:
        transaction_dir = group.workspace_dir / "transactions" / manifest.transaction_id
        shutil.rmtree(transaction_dir, ignore_errors=True)
    if group.group_dir is not None:
        shutil.rmtree(group.group_dir, ignore_errors=True)
    return len(group.transactions)
