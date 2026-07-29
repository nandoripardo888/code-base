"""Read-only Git adapter for change-set snapshots."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from code_harness.domain.enums import ChangeSourceKind, FileChangeKind
from code_harness.domain.errors import (
    ChangeSetNotFoundError,
    GitCommandFailedError,
    GitUnavailableError,
    InvalidChangeRequestError,
)
from code_harness.domain.models.change_set import (
    ChangedFile,
    ChangedHunk,
    ChangeDiff,
    ChangeSet,
    ChangeSetRequest,
)
from code_harness.infrastructure.git.unified_diff_parser import parse_unified_diff


class LocalGitChangeProvider:
    def __init__(self, root: str | Path, *, repository_id: str) -> None:
        self._root = Path(root).resolve(strict=False)
        self._repository_id = repository_id
        self._cache: dict[str, tuple[ChangeSet, ChangeDiff]] = {}
        self._lock = Lock()

    def create_change_set(self, request: ChangeSetRequest) -> ChangeSet:
        if request.source is ChangeSourceKind.WORKING_TREE:
            change_set, diff = self._working_tree(request)
        elif request.source is ChangeSourceKind.STAGED:
            change_set, diff = self._staged(request)
        else:
            raise InvalidChangeRequestError(
                f"Change source {request.source.value!r} is not supported yet.",
                source=request.source.value,
            )
        with self._lock:
            self._cache[change_set.change_set_id] = (change_set, diff)
        return change_set

    def get_change_set(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            cached = self._cache.get(change_set_id)
        if cached is None:
            raise ChangeSetNotFoundError(change_set_id)
        return cached[0]

    def read_diff(self, change_set: ChangeSet) -> ChangeDiff:
        with self._lock:
            cached = self._cache.get(change_set.change_set_id)
        if cached is None:
            raise ChangeSetNotFoundError(change_set.change_set_id)
        return cached[1]

    def _working_tree(self, request: ChangeSetRequest) -> tuple[ChangeSet, ChangeDiff]:
        base_ref = request.base or "HEAD"
        base_sha = self._rev_parse(base_ref)
        head_sha = base_sha
        tracked_diff = self._run_text(("diff", "--binary", base_ref, "--"))
        files = list(parse_unified_diff(tracked_diff))
        if request.include_untracked:
            for path in self._untracked_paths():
                files.append(self._untracked_as_added(path))
        return self._materialize(
            request=request,
            base_ref=base_ref,
            base_sha=base_sha,
            head_ref="WORKING_TREE",
            head_sha=head_sha,
            files=tuple(files),
            unified_text=tracked_diff,
        )

    def _staged(self, request: ChangeSetRequest) -> tuple[ChangeSet, ChangeDiff]:
        base_ref = request.base or "HEAD"
        base_sha = self._rev_parse(base_ref)
        staged_diff = self._run_text(("diff", "--cached", "--binary", base_ref, "--"))
        files = parse_unified_diff(staged_diff)
        return self._materialize(
            request=request,
            base_ref=base_ref,
            base_sha=base_sha,
            head_ref="INDEX",
            head_sha=self._run_text(("write-tree",)).strip() or None,
            files=files,
            unified_text=staged_diff,
        )

    def _materialize(
        self,
        *,
        request: ChangeSetRequest,
        base_ref: str | None,
        base_sha: str | None,
        head_ref: str | None,
        head_sha: str | None,
        files: tuple[ChangedFile, ...],
        unified_text: str,
    ) -> tuple[ChangeSet, ChangeDiff]:
        ordered = tuple(sorted(files, key=lambda item: item.path.casefold()))
        diff_sha256 = hashlib.sha256(unified_text.encode("utf-8", errors="replace")).hexdigest()
        identity = "|".join(
            (
                request.source.value,
                base_ref or "",
                base_sha or "",
                head_ref or "",
                head_sha or "",
                diff_sha256,
                *(f"{item.kind.value}:{item.path}:{item.old_path or ''}" for item in ordered),
            )
        )
        change_set_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        created_at = datetime.now(UTC).isoformat()
        change_set = ChangeSet(
            change_set_id=change_set_id,
            source_kind=request.source,
            repository_id=self._repository_id,
            base_ref=base_ref,
            base_sha=base_sha,
            head_ref=head_ref,
            head_sha=head_sha,
            diff_sha256=diff_sha256,
            files=tuple(
                ChangedFile(
                    path=item.path,
                    kind=item.kind,
                    old_path=item.old_path,
                    binary=item.binary,
                    old_sha256=item.old_sha256,
                    new_sha256=item.new_sha256,
                    hunks=(),
                )
                for item in ordered
            ),
            created_at=created_at,
        )
        diff = ChangeDiff(
            change_set_id=change_set_id,
            files=ordered,
            unified_text=unified_text,
        )
        return change_set, diff

    def _untracked_as_added(self, relative: str) -> ChangedFile:
        absolute = self._root / relative
        try:
            content = absolute.read_bytes()
        except OSError:
            content = b""
        text: str
        try:
            text = content.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            text = ""
            binary = True
        lines = text.splitlines()
        hunk_lines = (
            tuple(f"+{line}" for line in lines)
            if lines
            else ("+\\ No newline at end of file",)
        )
        hunk = ChangedHunk(
            hunk_id=hashlib.sha256(relative.encode()).hexdigest()[:16],
            old_start=0,
            old_count=0,
            new_start=1,
            new_count=len(lines) or 1,
            header=f"untracked {relative}",
            lines=hunk_lines if not binary else (),
        )
        return ChangedFile(
            path=relative.replace("\\", "/"),
            kind=FileChangeKind.BINARY if binary else FileChangeKind.ADDED,
            binary=binary,
            new_sha256=hashlib.sha256(content).hexdigest(),
            hunks=() if binary else (hunk,),
        )

    def _untracked_paths(self) -> tuple[str, ...]:
        output = self._run_bytes(
            ("ls-files", "-z", "--others", "--exclude-standard", "--", ".")
        )
        if not output:
            return ()
        return tuple(
            item.replace("\\", "/")
            for item in output.decode("utf-8", errors="surrogateescape").split("\0")
            if item
        )

    def _rev_parse(self, ref: str) -> str | None:
        try:
            value = self._run_text(("rev-parse", "--verify", ref)).strip()
        except GitCommandFailedError:
            return None
        return value or None

    def _run_text(self, args: tuple[str, ...]) -> str:
        return self._run_bytes(args).decode("utf-8", errors="replace")

    def _run_bytes(self, args: tuple[str, ...]) -> bytes:
        git = shutil.which("git")
        if git is None:
            raise GitUnavailableError()
        try:
            completed = subprocess.run(
                (git, "-C", str(self._root), *args),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandFailedError(f"Git command failed: {' '.join(args)}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandFailedError(
                f"Git command failed: {' '.join(args)}",
                stderr=stderr or None,
            )
        return completed.stdout
