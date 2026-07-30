from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from code_harness.domain.errors import ChangeSessionConflictError, ChangeSessionPathRejectedError
from code_harness.domain.models.change_segment import GitChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange
from code_harness.domain.protocols.blob_store import BlobStore


@dataclass(frozen=True, slots=True)
class _State:
    content: bytes | None
    mode: int | None

    @property
    def digest(self) -> str | None:
        return hashlib.sha256(self.content).hexdigest() if self.content is not None else None


class GitWorkspacePatchIntegrator:
    def __init__(self, *, blob_store: BlobStore, sessions_home: Path) -> None:
        self._blobs = blob_store
        self._sessions_home = Path(sessions_home)

    def preflight(
        self,
        detail: GitChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None:
        self._plan(detail, proposed)

    def integrate(
        self,
        detail: GitChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
    ) -> None:
        del session_id
        root = Path(detail.repository_root).resolve(strict=False)
        before, desired = self._plan(detail, proposed)
        applied: list[str] = []
        try:
            for path in sorted(desired):
                if desired[path] == before[path]:
                    continue
                target = self._guard(root, path)
                state = desired[path]
                if state.content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.code-harness-{uuid4().hex}.tmp")
                    temporary.write_bytes(state.content)
                    os.replace(temporary, target)
                    if state.mode is not None:
                        with suppress(OSError):
                            target.chmod(stat.S_IMODE(state.mode))
                applied.append(path)
        except Exception:
            for path in reversed(applied):
                target = self._guard(root, path)
                original = before[path]
                if original.content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original.content)
                    if original.mode is not None:
                        with suppress(OSError):
                            target.chmod(stat.S_IMODE(original.mode))
            raise

    def _plan(
        self,
        detail: GitChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> tuple[dict[str, _State], dict[str, _State]]:
        root = Path(detail.repository_root).resolve(strict=False)
        before: dict[str, _State] = {}
        desired: dict[str, _State] = {}
        conflicts: list[dict[str, object]] = []
        for change in proposed:
            target = self._guard(root, change.path)
            current = self._read(target)
            before[change.path] = current
            base = self._blobs.get(change.base_blob_id) if change.base_blob_id is not None else None
            candidate = (
                self._blobs.get(change.proposed_blob_id)
                if change.proposed_blob_id is not None
                else None
            )
            if current.digest == change.base_sha256:
                desired[change.path] = _State(candidate, change.proposed_mode)
                continue
            if current.digest == change.proposed_sha256:
                desired[change.path] = current
                continue
            if base is None or candidate is None or current.content is None:
                conflicts.append(self._conflict(change, current, "add_delete_conflict"))
                continue
            merged = self._merge_text(base, current.content, candidate)
            if merged is None:
                conflicts.append(self._conflict(change, current, "content_conflict"))
                continue
            desired[change.path] = _State(merged, change.proposed_mode or current.mode)
        if conflicts:
            raise ChangeSessionConflictError(
                "Workspace changes conflict with the prepared candidate.",
                conflicts=conflicts,
            )
        return before, desired

    def _merge_text(self, base: bytes, current: bytes, candidate: bytes) -> bytes | None:
        if any(b"\0" in item for item in (base, current, candidate)):
            return None
        try:
            base.decode("utf-8")
            current.decode("utf-8")
            candidate.decode("utf-8")
        except UnicodeDecodeError:
            return None
        git = shutil.which("git")
        if git is None:
            return None
        self._sessions_home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._sessions_home) as directory:
            root = Path(directory)
            ours = root / "current"
            ancestor = root / "base"
            theirs = root / "candidate"
            ours.write_bytes(current)
            ancestor.write_bytes(base)
            theirs.write_bytes(candidate)
            completed = subprocess.run(
                (git, "merge-file", "-p", str(ours), str(ancestor), str(theirs)),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
            if completed.returncode != 0:
                return None
            return completed.stdout

    @staticmethod
    def _conflict(
        change: ProposedFileChange,
        current: _State,
        kind: str,
    ) -> dict[str, object]:
        return {
            "path": change.path,
            "kind": kind,
            "base_sha256": change.base_sha256,
            "current_sha256": current.digest,
            "proposed_sha256": change.proposed_sha256,
        }

    @staticmethod
    def _read(path: Path) -> _State:
        if not path.is_file():
            return _State(None, None)
        metadata = path.stat()
        return _State(path.read_bytes(), stat.S_IMODE(metadata.st_mode))

    @staticmethod
    def _guard(root: Path, raw: str) -> Path:
        pure = PurePosixPath(raw.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or any(part == ".git" for part in pure.parts):
            raise ChangeSessionPathRejectedError(raw, "candidate path escapes repository")
        target = root.joinpath(*pure.parts)
        try:
            target.parent.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise ChangeSessionPathRejectedError(
                raw,
                "candidate path escapes repository",
            ) from error
        if target.is_symlink():
            raise ChangeSessionPathRejectedError(raw, "candidate target is a symlink")
        return target
