from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from code_harness.application.changes.create_session import aggregate_session_digest
from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.errors import ChangeSessionInvalidStateError, GitCommandFailedError
from code_harness.domain.models.change_segment import ChangeSessionSegment, GitChangeSegment
from code_harness.domain.models.change_session import (
    ChangeSession,
    ChangeSessionDiff,
    ChangeSessionEvent,
)
from code_harness.domain.models.workspace_manifest import MirrorPrepareResult
from code_harness.domain.protocols.change_isolation import GitWorktreeManager, WorkspaceMirrorPort
from code_harness.domain.protocols.change_session_store import ChangeSessionStore


def compute_candidate_digest(
    *,
    session_id: str,
    segment_id: str,
    repository_root: str,
    base_sha: str,
    target_branch: str,
    temporary_branch: str,
    candidate_commit: str,
    files: tuple[str, ...],
    diff_text: str,
) -> str:
    diff_hash = hashlib.sha256(diff_text.encode("utf-8", errors="replace")).hexdigest()
    payload = "|".join(
        (
            session_id,
            segment_id,
            repository_root,
            base_sha,
            target_branch,
            temporary_branch,
            candidate_commit,
            ",".join(files),
            diff_hash,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PrepareChangeSessionTool:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        sessions_home: Path,
        worktrees: GitWorktreeManager,
        mirrors: WorkspaceMirrorPort | None = None,
    ) -> None:
        self._store = store
        self._sessions_home = Path(sessions_home)
        self._worktrees = worktrees
        self._mirrors = mirrors

    def run(
        self,
        session_id: str,
        *,
        message: str = "code-harness change session candidate",
        allowed_paths: tuple[str, ...] | None = None,
        segment_id: str | None = None,
    ) -> tuple[ChangeSession, ChangeSessionDiff]:
        session = self._store.get_session(session_id)
        if session.status not in {
            ChangeSessionStatus.READY,
            ChangeSessionStatus.AGENT_WORKING,
        }:
            raise ChangeSessionInvalidStateError(
                f"Cannot prepare session in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        targets = session.segments
        if segment_id is not None:
            targets = tuple(item for item in session.segments if item.segment_id == segment_id)
            if not targets:
                raise ChangeSessionInvalidStateError(
                    f"Unknown segment_id {segment_id!r}.",
                    session_id=session_id,
                )

        updated_segments: list[ChangeSessionSegment] = list(session.segments)
        new_git: list[GitChangeSegment] = list(session.git_details)
        new_mirrors = list(session.mirror_details)
        segment_digests: list[str] = []
        all_files: list[str] = []
        unified_parts: list[str] = []
        prepared_segment_id = targets[0].segment_id

        for target in targets:
            if target.kind is ChangeSegmentKind.GIT_WORKTREE:
                detail = next(
                    (
                        item
                        for item in session.git_details
                        if item.worktree_path == target.isolation_root
                    ),
                    None,
                )
                if detail is None:
                    raise ChangeSessionInvalidStateError(
                        "Missing git detail for segment.",
                        segment_id=target.segment_id,
                    )
                try:
                    commit, diff_text, files = self._worktrees.prepare_candidate_commit(
                        worktree_path=Path(detail.worktree_path),
                        message=message,
                        allowed_paths=allowed_paths,
                    )
                except GitCommandFailedError:
                    if len(targets) > 1:
                        continue
                    raise
                digest = compute_candidate_digest(
                    session_id=session.session_id,
                    segment_id=target.segment_id,
                    repository_root=detail.repository_root,
                    base_sha=detail.base_sha,
                    target_branch=detail.target_branch,
                    temporary_branch=detail.temporary_branch,
                    candidate_commit=commit,
                    files=files,
                    diff_text=diff_text,
                )
                updated_detail = GitChangeSegment(
                    repository_root=detail.repository_root,
                    git_common_dir=detail.git_common_dir,
                    target_branch=detail.target_branch,
                    base_sha=detail.base_sha,
                    temporary_branch=detail.temporary_branch,
                    worktree_path=detail.worktree_path,
                    candidate_commit=commit,
                )
                new_git = [
                    updated_detail if item.worktree_path == detail.worktree_path else item
                    for item in new_git
                ]
                segment_digests.append(digest)
                all_files.extend(files)
                unified_parts.append(diff_text)
                prepared_segment_id = target.segment_id
                updated_segments = [
                    ChangeSessionSegment(
                        segment_id=item.segment_id,
                        kind=item.kind,
                        relative_root=item.relative_root,
                        source_root=item.source_root,
                        isolation_root=item.isolation_root,
                        status=(
                            ChangeSessionStatus.REVIEW_PENDING.value
                            if item.segment_id == target.segment_id
                            else item.status
                        ),
                        base_digest=item.base_digest,
                        candidate_digest=(
                            digest if item.segment_id == target.segment_id else item.candidate_digest
                        ),
                    )
                    for item in updated_segments
                ]
                continue

            if self._mirrors is None:
                raise ChangeSessionInvalidStateError("Mirror support is not configured.")
            detail = next(
                (
                    item
                    for item in session.mirror_details
                    if item.mirror_root == target.isolation_root
                ),
                None,
            )
            if detail is None:
                raise ChangeSessionInvalidStateError(
                    "Missing mirror detail for segment.",
                    segment_id=target.segment_id,
                )
            result = self._mirrors.prepare(
                session_id=session.session_id,
                segment_id=target.segment_id,
                source_root=Path(detail.source_root),
                mirror_root=Path(detail.mirror_root),
                base_manifest_digest=detail.base_manifest_digest,
                sessions_home=self._sessions_home,
            )
            assert isinstance(result, MirrorPrepareResult)
            self._store.save_proposed_files(
                session.session_id,
                target.segment_id,
                result.proposed_changes,
            )
            new_mirrors = [
                result.detail if item.mirror_root == detail.mirror_root else item
                for item in new_mirrors
            ]
            segment_digests.append(result.candidate_digest)
            all_files.extend(result.files)
            unified_parts.append(result.unified_text)
            prepared_segment_id = target.segment_id
            updated_segments = [
                ChangeSessionSegment(
                    segment_id=item.segment_id,
                    kind=item.kind,
                    relative_root=item.relative_root,
                    source_root=item.source_root,
                    isolation_root=item.isolation_root,
                    status=(
                        ChangeSessionStatus.REVIEW_PENDING.value
                        if item.segment_id == target.segment_id
                        else item.status
                    ),
                    base_digest=item.base_digest,
                    candidate_digest=(
                        result.candidate_digest
                        if item.segment_id == target.segment_id
                        else item.candidate_digest
                    ),
                )
                for item in updated_segments
            ]

        if not segment_digests:
            raise ChangeSessionInvalidStateError(
                "No segment changes were prepared.",
                session_id=session_id,
            )
        session_digest = (
            segment_digests[0]
            if len(segment_digests) == 1
            else aggregate_session_digest(tuple(segment_digests))
        )
        now = datetime.now(UTC).isoformat()
        prepared = ChangeSession(
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            workspace_root=session.workspace_root,
            topology_kind=session.topology_kind,
            status=ChangeSessionStatus.REVIEW_PENDING,
            created_at=session.created_at,
            updated_at=now,
            expires_at=session.expires_at,
            segments=tuple(updated_segments),
            candidate_digest=session_digest,
            approval_id=None,
            warnings=session.warnings,
            git_details=tuple(new_git),
            mirror_details=tuple(new_mirrors),
        )
        self._store.save_session(prepared)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="change_prepared",
                occurred_at=now,
                session_id=session_id,
                segment_id=prepared_segment_id,
                details={
                    "candidate_digest": session_digest,
                    "files": all_files,
                },
            )
        )
        diff = ChangeSessionDiff(
            session_id=session_id,
            candidate_digest=session_digest,
            segment_id=prepared_segment_id,
            unified_text="\n".join(unified_parts),
            files=tuple(dict.fromkeys(all_files)),
        )
        return prepared, diff
