"""Structured Git commit helper for review actions (no shell=True)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from code_harness.domain.errors import GitCommandFailedError, GitUnavailableError
from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import ReviewCommitResult


class GitReviewCommitter:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve(strict=False)

    def create_commit(
        self,
        *,
        change_set: ChangeSet,
        message: str,
        workspace_snapshot_digest: str,
        paths: tuple[str, ...] | None = None,
    ) -> ReviewCommitResult:
        target_paths = paths or tuple(item.path for item in change_set.files)
        if not target_paths:
            raise GitCommandFailedError("No paths available to commit.")
        self._run(("add", "--", *target_paths))
        self._run(("commit", "-m", message, "--", *target_paths))
        commit_sha = self._run(("rev-parse", "HEAD")).strip()
        return ReviewCommitResult(
            commit_sha=commit_sha,
            change_set_id=change_set.change_set_id,
            message=message,
            workspace_snapshot_digest=workspace_snapshot_digest,
        )

    def _run(self, args: tuple[str, ...]) -> str:
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        try:
            completed = subprocess.run(
                (git, "-C", str(self._root), *args),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandFailedError(f"Git command failed: {' '.join(args)}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandFailedError(
                f"Git command failed: {' '.join(args)}",
                stderr=stderr or None,
            )
        return completed.stdout.decode("utf-8", errors="replace")
