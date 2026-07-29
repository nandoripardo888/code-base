"""Optional isolated Git worktree for validation runs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import TracebackType

from code_harness.domain.errors import GitCommandFailedError, GitUnavailableError


class IsolatedGitWorktree:
    def __init__(
        self,
        root: str | Path,
        *,
        base_ref: str | None,
        unified_diff: str,
        change_set_id: str,
    ) -> None:
        self._root = Path(root).resolve(strict=False)
        self._base_ref = base_ref or "HEAD"
        self._unified_diff = unified_diff
        self._change_set_id = change_set_id
        self._path: Path | None = None

    def __enter__(self) -> Path:
        parent = self._root / ".code-harness" / "worktrees"
        parent.mkdir(parents=True, exist_ok=True)
        worktree = parent / f"validate-{self._change_set_id[:12]}"
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
            try:
                self._run(("worktree", "prune"))
            except GitCommandFailedError:
                pass
        self._run(("worktree", "add", "--detach", str(worktree), self._base_ref))
        self._path = worktree
        if self._unified_diff.strip():
            self._apply_diff(worktree, self._unified_diff)
        return worktree

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._path is None:
            return
        try:
            self._run(("worktree", "remove", "--force", str(self._path)))
        except GitCommandFailedError:
            shutil.rmtree(self._path, ignore_errors=True)
            try:
                self._run(("worktree", "prune"))
            except GitCommandFailedError:
                pass
        self._path = None

    def _apply_diff(self, worktree: Path, unified_diff: str) -> None:
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".patch",
            delete=False,
            newline="\n",
        ) as handle:
            handle.write(unified_diff)
            if not unified_diff.endswith("\n"):
                handle.write("\n")
            patch_path = Path(handle.name)
        try:
            completed = subprocess.run(
                (git, "-C", str(worktree), "apply", "--whitespace=nowarn", str(patch_path)),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandFailedError("Failed to apply change set into worktree.") from error
        finally:
            patch_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandFailedError(
                "Failed to apply change set into worktree.",
                stderr=stderr or None,
            )

    def _run(self, args: tuple[str, ...]) -> None:
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


class IsolatedGitWorktreeFactory:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve(strict=False)

    def create(
        self,
        *,
        change_set_id: str,
        base_ref: str | None,
        unified_diff: str,
    ) -> IsolatedGitWorktree:
        return IsolatedGitWorktree(
            self._root,
            base_ref=base_ref,
            unified_diff=unified_diff,
            change_set_id=change_set_id,
        )
