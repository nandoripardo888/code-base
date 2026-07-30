from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

import pathspec

from code_harness.domain.errors import (
    ChangeSessionPathRejectedError,
    ChangeSessionWorkspaceDirtyError,
    GitCommandFailedError,
)
from code_harness.domain.models.change_segment import GitChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange
from code_harness.domain.protocols.blob_store import BlobStore
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
    def __init__(
        self,
        *,
        require_clean: bool = False,
        sessions_home: Path | None = None,
        blob_store: BlobStore | None = None,
    ) -> None:
        self._require_clean = require_clean
        self._sessions_home = sessions_home
        self._blobs = blob_store

    def create(
        self,
        *,
        session_id: str,
        segment_id: str,
        repository_root: Path,
        sessions_home: Path,
    ) -> GitChangeSegment:
        total_started = perf_counter_ns()
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
        branch = client.current_branch_or_empty()
        source_head = client.rev_parse("HEAD")
        common_dir = client.common_dir()
        worktree = assert_under_sessions_home(
            sessions_home / session_id / "repos" / segment_id / "worktree",
            sessions_home,
        )
        if worktree.exists():
            raise ChangeSessionPathRejectedError(
                str(worktree),
                "worktree path already exists",
            )
        snapshot_index = sessions_home / session_id / "repos" / segment_id / "snapshot.index"
        snapshot_started = perf_counter_ns()
        baseline_commit, baseline_tree = client.create_workspace_snapshot_commit(
            index_path=snapshot_index,
            message=f"code-harness workspace snapshot {session_id}/{segment_id}",
        )
        snapshot_ms = _elapsed_ms(snapshot_started)
        try:
            worktree_started = perf_counter_ns()
            client.worktree_add_detached(worktree, baseline_commit)
            worktree_ms = _elapsed_ms(worktree_started)
            includes_started = perf_counter_ns()
            self._copy_worktree_includes(repository_root, worktree)
            includes_ms = _elapsed_ms(includes_started)
        except Exception:
            client.worktree_remove(worktree, force=True)
            raise
        return GitChangeSegment(
            repository_root=str(Path(repository_root).resolve(strict=False)),
            git_common_dir=common_dir,
            target_branch=branch,
            base_sha=baseline_commit,
            temporary_branch="",
            worktree_path=str(worktree),
            candidate_commit=None,
            integration_strategy="workspace_patch_v2",
            source_head_sha=source_head,
            baseline_commit=baseline_commit,
            baseline_tree=baseline_tree,
            timings_ms={
                "snapshot": snapshot_ms,
                "worktree_add": worktree_ms,
                "copy_includes": includes_ms,
                "total": _elapsed_ms(total_started),
            },
        )

    def remove(
        self,
        *,
        repository_root: Path,
        worktree_path: Path,
        temporary_branch: str,
        sessions_home: Path,
    ) -> None:
        if temporary_branch and not _TEMP_BRANCH_RE.match(temporary_branch):
            raise ChangeSessionPathRejectedError(
                temporary_branch,
                "temporary branch does not match code-harness/session pattern",
            )
        worktree = assert_under_sessions_home(Path(worktree_path), sessions_home)
        client = GitClient(repository_root)
        if worktree.exists():
            client.worktree_remove(worktree, force=True)
        if temporary_branch:
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
        baseline_commit = client.rev_parse("HEAD")
        index_path = worktree.parent / "candidate.index"
        commit, _tree = client.create_candidate_commit(
            cwd=worktree,
            baseline_commit=baseline_commit,
            index_path=index_path,
            message=message,
        )
        parent_diff = client.diff("--binary", f"{baseline_commit}", commit, cwd=worktree)
        named = client.run(
            ("diff-tree", "--no-commit-id", "--name-only", "-r", commit),
            cwd=worktree,
        ).stdout
        files = (
            tuple(line.replace("\\", "/").strip() for line in named.splitlines() if line.strip())
            or changed
        )
        return commit, parent_diff, tuple(files)

    def build_proposed_changes(
        self,
        *,
        session_id: str,
        worktree_path: Path,
        base_commit: str,
        candidate_commit: str,
        files: tuple[str, ...],
    ) -> tuple[ProposedFileChange, ...]:
        if self._blobs is None:
            raise RuntimeError("Blob store is required for workspace_patch_v2.")
        client = GitClient(worktree_path)
        changes: list[ProposedFileChange] = []
        for path in sorted(files):
            base = client.show_filtered_bytes(base_commit, path)
            proposed = client.show_filtered_bytes(candidate_commit, path)
            if base == proposed:
                continue
            operation = "added" if base is None else "deleted" if proposed is None else "modified"
            base_blob = (
                self._blobs.put(base, ref_owner=session_id, ref_kind="candidate")
                if base is not None
                else None
            )
            proposed_blob = (
                self._blobs.put(proposed, ref_owner=session_id, ref_kind="candidate")
                if proposed is not None
                else None
            )
            changes.append(
                ProposedFileChange(
                    path=path,
                    operation=operation,
                    base_sha256=hashlib.sha256(base).hexdigest() if base is not None else None,
                    proposed_sha256=(
                        hashlib.sha256(proposed).hexdigest() if proposed is not None else None
                    ),
                    base_blob_id=base_blob,
                    proposed_blob_id=proposed_blob,
                    base_mode=client.mode_at(base_commit, path),
                    proposed_mode=client.mode_at(candidate_commit, path),
                )
            )
        return tuple(changes)

    def preview_diff(
        self,
        *,
        worktree_path: Path,
    ) -> tuple[str, tuple[str, ...]]:
        worktree = Path(worktree_path).resolve(strict=False)
        if self._sessions_home is not None:
            assert_under_sessions_home(worktree, self._sessions_home)
        client = GitClient(worktree)
        baseline_commit = client.rev_parse("HEAD")
        index_path = worktree.parent / f"preview-{uuid4().hex}.index"
        env = {"GIT_INDEX_FILE": str(index_path.resolve(strict=False))}
        try:
            client.run(("read-tree", baseline_commit), cwd=worktree, env_extra=env)
            client.run(("add", "-A"), cwd=worktree, env_extra=env)
            unified = client.run(
                ("diff", "--cached", "--binary", baseline_commit),
                cwd=worktree,
                env_extra=env,
            ).stdout
            named = client.run(
                ("diff", "--cached", "--name-only", baseline_commit),
                cwd=worktree,
                env_extra=env,
            ).stdout
            files = tuple(
                line.replace("\\", "/").strip() for line in named.splitlines() if line.strip()
            )
            return unified, files
        finally:
            index_path.unlink(missing_ok=True)

    def prepared_diff(
        self,
        *,
        repository_root: Path,
        baseline_commit: str,
        candidate_commit: str,
    ) -> tuple[str, tuple[str, ...]]:
        client = GitClient(repository_root)
        if not client.commit_exists(candidate_commit):
            raise GitCommandFailedError("Prepared candidate commit is unavailable.")
        unified = client.diff("--binary", baseline_commit, candidate_commit)
        named = client.run(("diff", "--name-only", baseline_commit, candidate_commit)).stdout
        files = tuple(
            line.replace("\\", "/").strip() for line in named.splitlines() if line.strip()
        )
        return unified, files

    def _copy_worktree_includes(self, source: Path, destination: Path) -> None:
        include = source / ".worktreeinclude"
        if not include.is_file():
            return
        lines = [
            line
            for line in include.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not lines:
            return
        matcher = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        ignored = (
            GitClient(source)
            .run(("ls-files", "--others", "--ignored", "--exclude-standard", "-z"))
            .stdout.split("\0")
        )
        for raw in ignored:
            relative = raw.replace("\\", "/").strip()
            if not relative or not matcher.match_file(relative):
                continue
            source_path = source / relative
            target_path = destination / relative
            if source_path.is_symlink() or target_path.exists():
                continue
            if source_path.is_file():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)


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


def _elapsed_ms(started_ns: int) -> float:
    return max(0.0, (perf_counter_ns() - started_ns) / 1_000_000.0)
