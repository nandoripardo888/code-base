from __future__ import annotations

from pathlib import Path

from code_harness.domain.errors import (
    ChangeSessionConflictError,
    ChangeSessionStaleError,
    ChangeSessionWorkspaceDirtyError,
)
from code_harness.domain.models.change_segment import GitChangeSegment
from code_harness.infrastructure.changes.git.git_client import GitClient


class GitChangeIntegrator:
    def preflight_git(self, detail: GitChangeSegment) -> None:
        client = GitClient(detail.repository_root)
        if client.has_ongoing_operation():
            raise ChangeSessionWorkspaceDirtyError(
                "Repository has a Git operation in progress.",
                reason="git_operation_in_progress",
            )
        if not client.is_clean():
            raise ChangeSessionWorkspaceDirtyError(
                "Main working tree is dirty.",
                reason="workspace_dirty",
            )
        current = client.current_branch()
        if current != detail.target_branch:
            raise ChangeSessionStaleError(
                "Target branch changed since the session was created.",
                expected_branch=detail.target_branch,
                actual_branch=current,
            )
        head = client.rev_parse("HEAD")
        if head != detail.base_sha:
            raise ChangeSessionStaleError(
                "Repository HEAD moved away from the session base SHA.",
                expected_sha=detail.base_sha,
                actual_sha=head,
            )
        if detail.candidate_commit is None:
            raise ChangeSessionStaleError("Candidate commit is missing.")
        if not client.commit_exists(detail.candidate_commit):
            raise ChangeSessionStaleError(
                "Candidate commit no longer exists.",
                candidate_commit=detail.candidate_commit,
            )
        if not client.branch_points_at(detail.temporary_branch, detail.candidate_commit):
            raise ChangeSessionStaleError(
                "Temporary branch no longer points at the candidate commit.",
                temporary_branch=detail.temporary_branch,
                candidate_commit=detail.candidate_commit,
            )

    def integrate_git(self, detail: GitChangeSegment) -> str:
        self.preflight_git(detail)
        assert detail.candidate_commit is not None
        client = GitClient(detail.repository_root)
        result = client.cherry_pick(detail.candidate_commit)
        if result.returncode != 0:
            client.cherry_pick_abort()
            raise ChangeSessionConflictError(
                "Cherry-pick conflict while integrating the candidate commit.",
                candidate_commit=detail.candidate_commit,
                stderr=result.stderr.strip() or None,
            )
        return client.rev_parse("HEAD")


def assert_cwd_is_isolation_root(cwd: Path | str, isolation_root: Path | str) -> Path:
    resolved = Path(cwd).resolve(strict=False)
    expected = Path(isolation_root).resolve(strict=False)
    if resolved != expected:
        from code_harness.domain.errors import ChangeSessionPathRejectedError

        raise ChangeSessionPathRejectedError(
            str(cwd),
            "cwd must equal the session isolation root",
        )
    return resolved
