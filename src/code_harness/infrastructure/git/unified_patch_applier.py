"""Apply unified patches with preflight hash checks via Git."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from code_harness.domain.enums import ChangeSourceKind
from code_harness.domain.errors import (
    GitUnavailableError,
    ReviewPatchApplyFailedError,
    WorkspaceSnapshotMismatchError,
)
from code_harness.domain.models.change_set import ChangeSet, ChangeSetRequest
from code_harness.domain.models.review_actions import ReviewFixApplication
from code_harness.infrastructure.git.local_git_change_provider import LocalGitChangeProvider
from code_harness.infrastructure.git.workspace_snapshot import GitWorkspaceSnapshotProvider


class UnifiedPatchApplier:
    def __init__(
        self,
        root: str | Path,
        *,
        change_provider: LocalGitChangeProvider,
        snapshot_provider: GitWorkspaceSnapshotProvider | None = None,
    ) -> None:
        self._root = Path(root).resolve(strict=False)
        self._change_provider = change_provider
        self._snapshots = snapshot_provider or GitWorkspaceSnapshotProvider(self._root)

    def apply_unified_patch(
        self,
        *,
        change_set: ChangeSet,
        patch_text: str,
        expected_file_hashes: tuple[tuple[str, str], ...],
    ) -> ReviewFixApplication:
        if not patch_text.strip():
            raise ReviewPatchApplyFailedError("patch_text must not be empty")
        current = self._snapshots.snapshot_for_change_set(change_set)
        expected = dict(expected_file_hashes)
        mismatches: list[str] = []
        for path, expected_hash in sorted(expected.items()):
            absolute = self._root / path
            actual = (
                hashlib.sha256(absolute.read_bytes()).hexdigest()
                if absolute.is_file()
                else "missing"
            )
            if actual != expected_hash:
                mismatches.append(path)
        if mismatches:
            raise WorkspaceSnapshotMismatchError(
                "Workspace files diverged from the approved snapshot.",
                mismatched_paths=mismatches,
                change_set_id=change_set.change_set_id,
                workspace_snapshot_digest=current.digest,
            )
        self._git_apply(patch_text, check_only=True)
        self._git_apply(patch_text, check_only=False)
        new_change_set = self._change_provider.create_change_set(
            ChangeSetRequest(
                source=ChangeSourceKind.WORKING_TREE,
                base=change_set.base_ref or "HEAD",
                include_untracked=True,
            )
        )
        applied_paths = tuple(
            sorted({path.replace("\\", "/") for path, _hash in expected_file_hashes})
        )
        return ReviewFixApplication(
            base_change_set_id=change_set.change_set_id,
            workspace_snapshot_digest=current.digest,
            applied_paths=applied_paths,
            patch_sha256=hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
            new_change_set=new_change_set,
        )

    def _git_apply(self, patch_text: str, *, check_only: bool) -> None:
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
            handle.write(patch_text)
            if not patch_text.endswith("\n"):
                handle.write("\n")
            patch_path = Path(handle.name)
        args = [git, "-C", str(self._root), "apply"]
        if check_only:
            args.append("--check")
        args.extend(("--whitespace=nowarn", str(patch_path)))
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReviewPatchApplyFailedError("Git apply failed to start.") from error
        finally:
            patch_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            action = "check" if check_only else "apply"
            raise ReviewPatchApplyFailedError(
                f"Git apply --{action} failed.",
                stderr=stderr or None,
            )
