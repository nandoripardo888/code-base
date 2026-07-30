from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from code_harness.domain.errors import GitCommandFailedError, GitUnavailableError


@dataclass(frozen=True, slots=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


class GitClient:
    def __init__(self, repository_root: Path | str, *, timeout_seconds: float = 60.0) -> None:
        self._root = Path(repository_root).resolve(strict=False)
        self._timeout = timeout_seconds

    @property
    def root(self) -> Path:
        return self._root

    def run(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        check: bool = True,
        env_extra: dict[str, str] | None = None,
    ) -> GitResult:
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        workdir = cwd or self._root
        env = None
        if env_extra:
            import os

            env = {**os.environ, **env_extra}
        try:
            completed = subprocess.run(
                (git, "-C", str(workdir), *args),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=self._timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandFailedError(f"Git command failed: {' '.join(args)}") from error
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        result = GitResult(returncode=completed.returncode, stdout=stdout, stderr=stderr)
        if check and result.returncode != 0:
            raise GitCommandFailedError(
                f"Git command failed: {' '.join(args)}",
                stderr=stderr.strip() or None,
            )
        return result

    def rev_parse(self, ref: str = "HEAD") -> str:
        return self.run(("rev-parse", ref)).stdout.strip()

    def current_branch(self) -> str:
        result = self.run(("symbolic-ref", "--short", "HEAD"), check=False)
        if result.returncode != 0:
            raise GitCommandFailedError(
                "HEAD is detached; an identifiable branch is required.",
                stderr=result.stderr.strip() or None,
            )
        return result.stdout.strip()

    def current_branch_or_empty(self) -> str:
        result = self.run(("symbolic-ref", "--short", "HEAD"), check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def is_clean(self) -> bool:
        status = self.run(("status", "--porcelain")).stdout.strip()
        return status == ""

    def has_ongoing_operation(self) -> bool:
        git_dir = Path(self.run(("rev-parse", "--git-dir")).stdout.strip())
        if not git_dir.is_absolute():
            git_dir = self._root / git_dir
        markers = (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REBASE_HEAD",
            "rebase-merge",
            "rebase-apply",
        )
        return any((git_dir / name).exists() for name in markers)

    def common_dir(self) -> str:
        common = Path(self.run(("rev-parse", "--git-common-dir")).stdout.strip())
        if not common.is_absolute():
            common = self._root / common
        return str(common.resolve(strict=False))

    def create_branch(self, name: str, start_point: str) -> None:
        self.run(("branch", name, start_point))

    def delete_branch(self, name: str, *, force: bool = True) -> None:
        flag = "-D" if force else "-d"
        self.run(("branch", flag, name), check=False)

    def worktree_add(self, path: Path, branch: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run(("worktree", "add", str(path), branch))

    def worktree_add_detached(self, path: Path, commit: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run(("worktree", "add", "--detach", str(path), commit))

    def worktree_remove(self, path: Path, *, force: bool = True) -> None:
        args: tuple[str, ...] = ("worktree", "remove", str(path))
        if force:
            args = ("worktree", "remove", "--force", str(path))
        self.run(args, check=False)

    def worktree_prune(self) -> None:
        self.run(("worktree", "prune", "--expire", "now"), check=False)

    def status_porcelain(self, *, cwd: Path | None = None) -> str:
        return self.run(("status", "--porcelain"), cwd=cwd).stdout

    def diff(self, *extra: str, cwd: Path | None = None) -> str:
        return self.run(("diff", *extra), cwd=cwd).stdout

    def add(self, paths: tuple[str, ...], *, cwd: Path | None = None) -> None:
        if not paths:
            self.run(("add", "-A"), cwd=cwd)
            return
        self.run(("add", "--", *paths), cwd=cwd)

    def commit(self, message: str, *, cwd: Path | None = None) -> str:
        self.run(("commit", "-m", message), cwd=cwd)
        return self.run(("rev-parse", "HEAD"), cwd=cwd).stdout.strip()

    def cherry_pick(self, commit: str) -> GitResult:
        return self.run(("cherry-pick", commit), check=False)

    def cherry_pick_abort(self) -> None:
        self.run(("cherry-pick", "--abort"), check=False)

    def commit_exists(self, commit: str) -> bool:
        result = self.run(("cat-file", "-e", f"{commit}^{{commit}}"), check=False)
        return result.returncode == 0

    def branch_points_at(self, branch: str, commit: str) -> bool:
        result = self.run(("rev-parse", branch), check=False)
        return result.returncode == 0 and result.stdout.strip() == commit

    def create_workspace_snapshot_commit(
        self,
        *,
        index_path: Path,
        message: str,
    ) -> tuple[str, str]:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.unlink(missing_ok=True)
        env = {
            "GIT_INDEX_FILE": str(index_path.resolve(strict=False)),
            "GIT_AUTHOR_NAME": "code-harness",
            "GIT_AUTHOR_EMAIL": "code-harness@local",
            "GIT_COMMITTER_NAME": "code-harness",
            "GIT_COMMITTER_EMAIL": "code-harness@local",
        }
        head = self.rev_parse("HEAD")
        try:
            self.run(("read-tree", head), env_extra=env)
            self.run(("add", "-A"), env_extra=env)
            tree = self.run(("write-tree",), env_extra=env).stdout.strip()
            commit = self.run(
                ("commit-tree", tree, "-p", head, "-m", message),
                env_extra=env,
            ).stdout.strip()
            return commit, tree
        finally:
            index_path.unlink(missing_ok=True)

    def create_candidate_commit(
        self,
        *,
        cwd: Path,
        baseline_commit: str,
        index_path: Path,
        message: str,
    ) -> tuple[str, str]:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.unlink(missing_ok=True)
        env = {
            "GIT_INDEX_FILE": str(index_path.resolve(strict=False)),
            "GIT_AUTHOR_NAME": "code-harness",
            "GIT_AUTHOR_EMAIL": "code-harness@local",
            "GIT_COMMITTER_NAME": "code-harness",
            "GIT_COMMITTER_EMAIL": "code-harness@local",
        }
        try:
            self.run(("read-tree", baseline_commit), cwd=cwd, env_extra=env)
            self.run(("add", "-A"), cwd=cwd, env_extra=env)
            tree = self.run(("write-tree",), cwd=cwd, env_extra=env).stdout.strip()
            commit = self.run(
                ("commit-tree", tree, "-p", baseline_commit, "-m", message),
                cwd=cwd,
                env_extra=env,
            ).stdout.strip()
            return commit, tree
        finally:
            index_path.unlink(missing_ok=True)

    def show_bytes(self, commit: str, path: str) -> bytes | None:
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        completed = subprocess.run(
            (git, "-C", str(self._root), "show", f"{commit}:{path}"),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=self._timeout,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout

    def show_filtered_bytes(self, commit: str, path: str) -> bytes | None:
        spec = f"{commit}:{path}"
        exists = self.run(("cat-file", "-e", spec), check=False)
        if exists.returncode != 0:
            return None
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        try:
            completed = subprocess.run(
                (
                    git,
                    "-C",
                    str(self._root),
                    "cat-file",
                    "--filters",
                    f"--path={path}",
                    spec,
                ),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandFailedError(
                f"Git command failed: cat-file --filters --path={path} {spec}"
            ) from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandFailedError(
                f"Git command failed: cat-file --filters --path={path} {spec}",
                stderr=stderr or None,
            )
        return completed.stdout

    def mode_at(self, commit: str, path: str) -> int | None:
        result = self.run(("ls-tree", commit, "--", path), check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        raw_mode = result.stdout.split(maxsplit=1)[0]
        try:
            return int(raw_mode, 8)
        except ValueError:
            return None
