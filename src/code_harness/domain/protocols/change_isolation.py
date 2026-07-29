from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Protocol

from code_harness.domain.models.change_segment import GitChangeSegment, MirrorChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange


class SessionLock(Protocol):
    def __enter__(self) -> SessionLock: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @property
    def key(self) -> str: ...


class SessionLockManager(Protocol):
    def acquire(self, key: str) -> SessionLock: ...


class GitWorktreeManager(Protocol):
    def create(
        self,
        *,
        session_id: str,
        segment_id: str,
        repository_root: Path,
        sessions_home: Path,
    ) -> GitChangeSegment: ...

    def remove(
        self,
        *,
        repository_root: Path,
        worktree_path: Path,
        temporary_branch: str,
        sessions_home: Path,
    ) -> None: ...

    def prepare_candidate_commit(
        self,
        *,
        worktree_path: Path,
        message: str,
        allowed_paths: tuple[str, ...] | None = None,
    ) -> tuple[str, str, tuple[str, ...]]:
        """Return (candidate_commit, unified_diff, changed_files)."""
        ...


class GitBranchReader(Protocol):
    def current_branch(self, repository_root: Path) -> str: ...


class WorkspaceMirrorPort(Protocol):
    def create_with_manifest(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        sessions_home: Path,
        include_relative_roots: tuple[str, ...] | None = None,
        exclude_git_roots: tuple[str, ...] = (),
    ) -> MirrorChangeSegment: ...

    def prepare(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        mirror_root: Path,
        base_manifest_digest: str,
        sessions_home: Path | None = None,
    ) -> object: ...


class MirrorIntegratorPort(Protocol):
    def preflight_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None: ...

    def integrate_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
        journal_path: Path | None = None,
    ) -> None: ...
