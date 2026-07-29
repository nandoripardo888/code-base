from __future__ import annotations

import re
from pathlib import Path

from code_harness.domain.errors import (
    ChangeSessionPathRejectedError,
    ChangeSessionWorkspaceDirtyError,
    GitCommandFailedError,
)
from code_harness.domain.models.change_segment import GitChangeSegment
from code_harness.infrastructure.changes.git.git_client import GitClient

_TEMP_BRANCH_RE = re.compile(r"^code-harness/session/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def temporary_branch_name(session_id: str, segment_id: str) -> str:
    return f"code-harness/session/{session_id}/{segment_id}"


def assert_under_sessions_home(path: Path, sessions_home: Path) -> Path:
    resolved = path.resolve(strict=False)
    home = sessions_home.resolve(strict=False)
    try:
        resolved.relative_to(home)
    except ValueError as error:
        raise ChangeSessionPathRejectedError(
            str(path),
            "path is not under change-sessions home",
        ) from error
    if resolved == home:
        raise ChangeSessionPathRejectedError(str(path), "refusing to operate on sessions home root")
    return resolved


class LocalGitWorktreeManager:
    def __init__(self, *, require_clean: bool = True, sessions_home: Path | None = None) -> None:
        self._require_clean = require_clean
        self._sessions_home = sessions_home

    def create(
        self,
        *,
        session_id: str,
        segment_id: str,
        repository_root: Path,
        sessions_home: Path,
    ) -> GitChangeSegment:
        client = GitClient(repository_root)
        if self._require_clean and not client.is_clean():
            raise ChangeSessionWorkspaceDirtyError(
                "Workspace working tree is dirty.",
                reason="workspace_dirty",
            )
        if client.has_ongoing_operation():
            raise ChangeSessionWorkspaceDirtyError(
                "Repository has a merge, rebase, or cherry-pick in progress.",
                reason="git_operation_in_progress",
            )
        branch = client.current_branch()
        base_sha = client.rev_parse("HEAD")
        common_dir = client.common_dir()
        temp_branch = temporary_branch_name(session_id, segment_id)
        worktree = assert_under_sessions_home(
            sessions_home / session_id / "repos" / segment_id / "worktree",
            sessions_home,
        )
        if worktree.exists():
            raise ChangeSessionPathRejectedError(
                str(worktree),
                "worktree path already exists",
            )
        client.create_branch(temp_branch, base_sha)
        try:
            client.worktree_add(worktree, temp_branch)
        except GitCommandFailedError:
            client.delete_branch(temp_branch, force=True)
            raise
        return GitChangeSegment(
            repository_root=str(Path(repository_root).resolve(strict=False)),
            git_common_dir=common_dir,
            target_branch=branch,
            base_sha=base_sha,
            temporary_branch=temp_branch,
            worktree_path=str(worktree),
            candidate_commit=None,
        )

    def remove(
        self,
        *,
        repository_root: Path,
        worktree_path: Path,
        temporary_branch: str,
        sessions_home: Path,
    ) -> None:
        if not _TEMP_BRANCH_RE.match(temporary_branch):
            raise ChangeSessionPathRejectedError(
                temporary_branch,
                "temporary branch does not match code-harness/session pattern",
            )
        worktree = assert_under_sessions_home(Path(worktree_path), sessions_home)
        client = GitClient(repository_root)
        if worktree.exists():
            client.worktree_remove(worktree, force=True)
        client.delete_branch(temporary_branch, force=True)
        client.worktree_prune()
        if worktree.exists():
            import shutil

            shutil.rmtree(worktree, ignore_errors=True)

    def prepare_candidate_commit(
        self,
        *,
        worktree_path: Path,
        message: str,
        allowed_paths: tuple[str, ...] | None = None,
    ) -> tuple[str, str, tuple[str, ...]]:
        worktree = Path(worktree_path).resolve(strict=False)
        if self._sessions_home is not None:
            assert_under_sessions_home(worktree, self._sessions_home)
        client = GitClient(worktree)
        status = client.status_porcelain(cwd=worktree)
        if not status.strip():
            raise GitCommandFailedError("No changes to prepare in the isolated worktree.")
        changed = _paths_from_porcelain(status)
        if allowed_paths is not None:
            allowed = {path.replace("\\", "/") for path in allowed_paths}
            unauthorized = [path for path in changed if path not in allowed]
            if unauthorized:
                raise GitCommandFailedError(
                    "Worktree contains paths outside the authorized set.",
                    stderr=", ".join(unauthorized),
                )
            client.add(tuple(sorted(allowed)), cwd=worktree)
        else:
            client.add((), cwd=worktree)
        unstaged = client.diff(cwd=worktree)
        staged = client.diff("--cached", cwd=worktree)
        unified = staged or unstaged
        commit = client.commit(message, cwd=worktree)
        parent_diff = client.diff(f"{commit}^!", cwd=worktree)
        named = client.run(
            ("diff-tree", "--no-commit-id", "--name-only", "-r", commit),
            cwd=worktree,
        ).stdout
        files = tuple(
            line.replace("\\", "/").strip() for line in named.splitlines() if line.strip()
        ) or changed
        return commit, parent_diff or unified, tuple(files)


class GitBranchReaderAdapter:
    def current_branch(self, repository_root: Path) -> str:
        return GitClient(repository_root).current_branch()


def _paths_from_porcelain(status: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        body = line[3:] if len(line) > 3 else line
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        paths.append(body.replace("\\", "/").strip())
    return tuple(dict.fromkeys(paths))
