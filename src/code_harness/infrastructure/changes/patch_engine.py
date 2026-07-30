from __future__ import annotations

import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeCheckpointStaleError,
    ChangeCheckpointUnavailableError,
    ChangePatchBinaryUnsupportedError,
    ChangePatchContextMismatchError,
    ChangePatchInvalidError,
    ChangeSessionInvalidStateError,
    ChangeSessionPathRejectedError,
)
from code_harness.domain.models.change_checkpoint import (
    ChangeCheckpoint,
    ChangeCheckpointFile,
    ChangePatchResult,
)
from code_harness.domain.models.change_segment import ChangeSessionSegment
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.blob_store import BlobStore
from code_harness.domain.protocols.change_isolation import SessionLockManager
from code_harness.domain.protocols.change_session_store import ChangeSessionStore


@dataclass(frozen=True, slots=True)
class _Hunk:
    header: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Operation:
    kind: str
    path: str
    move_to: str | None = None
    content: tuple[str, ...] = ()
    hunks: tuple[_Hunk, ...] = ()


@dataclass(frozen=True, slots=True)
class _FileState:
    content: bytes | None
    mode: int | None

    @property
    def sha256(self) -> str | None:
        if self.content is None:
            return None
        return hashlib.sha256(self.content).hexdigest()


class CodexPatchEngine:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        blob_store: BlobStore,
        locks: SessionLockManager,
    ) -> None:
        self._store = store
        self._blobs = blob_store
        self._locks = locks

    def apply(
        self,
        session_id: str,
        patch_text: str,
        *,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> ChangePatchResult:
        operations = _parse_patch(patch_text)
        session, segment = self._resolve_segment(session_id, segment_id)
        with self._locks.acquire(f"checkpoint:{session_id}:{segment.segment_id}"):
            session = self._store.get_session(session_id)
            if session.status not in {
                ChangeSessionStatus.READY,
                ChangeSessionStatus.AGENT_WORKING,
                ChangeSessionStatus.REVIEW_PENDING,
            }:
                raise ChangeSessionInvalidStateError(
                    "Patches require a ready, working, or review-pending session.",
                    session_id=session_id,
                    status=session.status.value,
                )
            active = self._store.get_active_checkpoint(session_id, segment.segment_id)
            if active is None:
                raise ChangeCheckpointUnavailableError("apply")
            self._assert_expected(active, expected_checkpoint_id)
            root = Path(segment.isolation_root).resolve(strict=False)
            before, after = self._evaluate(root, operations)
            checkpoint_files = self._checkpoint_files(session_id, before, after)
            if not checkpoint_files:
                raise ChangePatchInvalidError("Patch does not change any file.")
            self._write_transaction(root, before, after)

            existing = self._store.list_checkpoints(session_id, segment.segment_id)
            state_updates = {active.checkpoint_id: "applied"}
            for item in existing:
                if item.sequence > active.sequence and item.state == "undone":
                    state_updates[item.checkpoint_id] = "superseded"
            self._store.set_checkpoint_states(state_updates)
            checkpoint = ChangeCheckpoint(
                checkpoint_id=uuid4().hex,
                session_id=session_id,
                segment_id=segment.segment_id,
                parent_checkpoint_id=active.checkpoint_id,
                sequence=max((item.sequence for item in existing), default=0) + 1,
                state="active",
                kind="patch",
                patch_sha256=hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
                created_at=datetime.now(UTC).isoformat(),
                files=checkpoint_files,
            )
            self._store.save_checkpoint(checkpoint)
            self._mark_working(session)
            self._event(session_id, segment.segment_id, "checkpoint_created", checkpoint)
            return self._result(checkpoint, checkpoint.files)

    def list(
        self,
        session_id: str,
        *,
        segment_id: str | None = None,
    ) -> tuple[ChangeCheckpoint, ...]:
        _session, segment = self._resolve_segment(session_id, segment_id)
        return self._store.list_checkpoints(session_id, segment.segment_id)

    def undo(
        self,
        session_id: str,
        *,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> ChangePatchResult:
        _session, segment = self._resolve_segment(session_id, segment_id)
        with self._locks.acquire(f"checkpoint:{session_id}:{segment.segment_id}"):
            active = self._require_active(session_id, segment.segment_id)
            self._assert_expected(active, expected_checkpoint_id)
            if active.parent_checkpoint_id is None:
                raise ChangeCheckpointUnavailableError("undo")
            target = self._store.get_checkpoint(active.parent_checkpoint_id)
            return self._move(session_id, segment.segment_id, Path(segment.isolation_root), target)

    def redo(
        self,
        session_id: str,
        *,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> ChangePatchResult:
        _session, segment = self._resolve_segment(session_id, segment_id)
        with self._locks.acquire(f"checkpoint:{session_id}:{segment.segment_id}"):
            active = self._require_active(session_id, segment.segment_id)
            self._assert_expected(active, expected_checkpoint_id)
            candidates = [
                item
                for item in self._store.list_checkpoints(session_id, segment.segment_id)
                if item.parent_checkpoint_id == active.checkpoint_id and item.state == "undone"
            ]
            if len(candidates) != 1:
                raise ChangeCheckpointUnavailableError("redo")
            return self._move(
                session_id,
                segment.segment_id,
                Path(segment.isolation_root),
                candidates[0],
            )

    def restore(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        expected_checkpoint_id: str | None = None,
    ) -> ChangePatchResult:
        target = self._store.get_checkpoint(checkpoint_id)
        if target.session_id != session_id or target.state == "superseded":
            raise ChangeCheckpointUnavailableError(
                "restore",
                checkpoint_id=checkpoint_id,
            )
        session, segment = self._resolve_segment(session_id, target.segment_id)
        del session
        with self._locks.acquire(f"checkpoint:{session_id}:{segment.segment_id}"):
            active = self._require_active(session_id, segment.segment_id)
            self._assert_expected(active, expected_checkpoint_id)
            return self._move(
                session_id,
                segment.segment_id,
                Path(segment.isolation_root),
                target,
            )

    def _move(
        self,
        session_id: str,
        segment_id: str,
        root: Path,
        target: ChangeCheckpoint,
    ) -> ChangePatchResult:
        active = self._require_active(session_id, segment_id)
        if target.checkpoint_id == active.checkpoint_id:
            return self._result(active, ())
        checkpoints = self._store.list_checkpoints(session_id, segment_id)
        by_id = {item.checkpoint_id: item for item in checkpoints}
        if target.sequence < active.sequence:
            chain: list[ChangeCheckpoint] = []
            cursor = active
            while cursor.checkpoint_id != target.checkpoint_id:
                chain.append(cursor)
                if cursor.parent_checkpoint_id is None:
                    raise ChangeCheckpointUnavailableError("restore")
                parent = by_id.get(cursor.parent_checkpoint_id)
                if parent is None:
                    raise ChangeCheckpointStaleError(
                        "Checkpoint history is incomplete.",
                        checkpoint_id=cursor.checkpoint_id,
                    )
                cursor = parent
            direction = "backward"
        else:
            chain = []
            cursor = active
            while cursor.checkpoint_id != target.checkpoint_id:
                children = [
                    item
                    for item in checkpoints
                    if item.parent_checkpoint_id == cursor.checkpoint_id
                    and item.state == "undone"
                ]
                if len(children) != 1:
                    raise ChangeCheckpointUnavailableError("restore")
                cursor = children[0]
                chain.append(cursor)
                if cursor.sequence > target.sequence:
                    raise ChangeCheckpointUnavailableError("restore")
            direction = "forward"

        resolved_root = root.resolve(strict=False)
        touched = {file.path for item in chain for file in item.files}
        before = {path: self._read_state(self._guard_path(resolved_root, path)) for path in touched}
        simulated = dict(before)
        for item in chain:
            files = item.files
            for file in files:
                current = simulated[file.path]
                expected = file.after_sha256 if direction == "backward" else file.before_sha256
                if current.sha256 != expected:
                    raise ChangeCheckpointStaleError(
                        "Isolated file diverged from checkpoint history.",
                        path=file.path,
                        checkpoint_id=item.checkpoint_id,
                        expected_sha256=expected,
                        actual_sha256=current.sha256,
                    )
                blob_id = file.before_blob_id if direction == "backward" else file.after_blob_id
                mode = file.before_mode if direction == "backward" else file.after_mode
                simulated[file.path] = _FileState(
                    self._blobs.get(blob_id) if blob_id is not None else None,
                    mode,
                )
        self._write_transaction(resolved_root, before, simulated)
        states: dict[str, str] = {
            active.checkpoint_id: "undone" if direction == "backward" else "applied"
        }
        if direction == "backward":
            states.update({item.checkpoint_id: "undone" for item in chain})
        else:
            states.update({item.checkpoint_id: "applied" for item in chain})
        states[target.checkpoint_id] = "active"
        self._store.set_checkpoint_states(states)
        session = self._store.get_session(session_id)
        self._mark_working(session)
        self._event(session_id, segment_id, "checkpoint_restored", target)
        files = tuple(file for item in chain for file in item.files)
        return self._result(target, files)

    def _evaluate(
        self,
        root: Path,
        operations: tuple[_Operation, ...],
    ) -> tuple[dict[str, _FileState], dict[str, _FileState]]:
        before: dict[str, _FileState] = {}
        after: dict[str, _FileState] = {}

        def state(path: str) -> _FileState:
            if path in after:
                return after[path]
            target = self._guard_path(root, path)
            value = self._read_state(target)
            before.setdefault(path, value)
            after[path] = value
            return value

        for operation in operations:
            current = state(operation.path)
            if operation.kind == "add":
                if current.content is not None:
                    raise ChangePatchContextMismatchError(
                        operation.path,
                        "Cannot add a file that already exists.",
                    )
                content = "\n".join(operation.content)
                if operation.content:
                    content += "\n"
                after[operation.path] = _FileState(content.encode("utf-8"), 0o644)
                continue
            if operation.kind == "delete":
                if current.content is None:
                    raise ChangePatchContextMismatchError(
                        operation.path,
                        "Cannot delete a missing file.",
                    )
                after[operation.path] = _FileState(None, None)
                continue
            if current.content is None:
                raise ChangePatchContextMismatchError(
                    operation.path,
                    "Cannot update a missing file.",
                )
            updated = _apply_hunks(operation.path, current.content, operation.hunks)
            if operation.move_to is None:
                after[operation.path] = _FileState(updated, current.mode)
                continue
            destination = state(operation.move_to)
            if destination.content is not None:
                raise ChangePatchContextMismatchError(
                    operation.move_to,
                    "Move destination already exists.",
                )
            after[operation.path] = _FileState(None, None)
            after[operation.move_to] = _FileState(updated, current.mode)
        return before, after

    def _checkpoint_files(
        self,
        session_id: str,
        before: dict[str, _FileState],
        after: dict[str, _FileState],
    ) -> tuple[ChangeCheckpointFile, ...]:
        files: list[ChangeCheckpointFile] = []
        for path in sorted(after):
            old = before.get(path, _FileState(None, None))
            new = after[path]
            if old == new:
                continue
            operation = (
                "added"
                if old.content is None
                else "deleted"
                if new.content is None
                else "modified"
            )
            before_blob = (
                self._blobs.put(old.content, ref_owner=session_id, ref_kind="checkpoint")
                if old.content is not None
                else None
            )
            after_blob = (
                self._blobs.put(new.content, ref_owner=session_id, ref_kind="checkpoint")
                if new.content is not None
                else None
            )
            files.append(
                ChangeCheckpointFile(
                    path=path,
                    operation=operation,
                    before_sha256=old.sha256,
                    after_sha256=new.sha256,
                    before_blob_id=before_blob,
                    after_blob_id=after_blob,
                    before_mode=old.mode,
                    after_mode=new.mode,
                )
            )
        return tuple(files)

    def _write_transaction(
        self,
        root: Path,
        before: dict[str, _FileState],
        after: dict[str, _FileState],
    ) -> None:
        applied: list[str] = []
        try:
            for path in sorted(after):
                if before.get(path) == after[path]:
                    continue
                target = self._guard_path(root, path)
                desired = after[path]
                if desired.content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.code-harness-{uuid4().hex}.tmp")
                    temporary.write_bytes(desired.content)
                    os.replace(temporary, target)
                    if desired.mode is not None:
                        with suppress(OSError):
                            target.chmod(stat.S_IMODE(desired.mode))
                applied.append(path)
        except Exception:
            for path in reversed(applied):
                target = self._guard_path(root, path)
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

    def _guard_path(self, root: Path, raw: str) -> Path:
        normalized = raw.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            not normalized
            or pure.is_absolute()
            or ".." in pure.parts
            or any(":" in part for part in pure.parts)
            or any(part.casefold() == ".git" for part in pure.parts)
        ):
            raise ChangeSessionPathRejectedError(
                raw,
                "path must be relative and remain outside .git",
            )
        target = root.joinpath(*pure.parts)
        resolved_parent = target.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(root)
        except ValueError as error:
            raise ChangeSessionPathRejectedError(raw, "path escapes isolation root") from error
        current = root
        for part in pure.parts[:-1]:
            current /= part
            if current.exists() and _is_reparse(current):
                raise ChangeSessionPathRejectedError(
                    raw,
                    "path traverses a symlink or reparse point",
                )
        if target.exists() and _is_reparse(target):
            raise ChangeSessionPathRejectedError(raw, "target is a symlink or reparse point")
        return target

    @staticmethod
    def _read_state(path: Path) -> _FileState:
        if not path.is_file():
            return _FileState(None, None)
        metadata = path.stat()
        return _FileState(path.read_bytes(), stat.S_IMODE(metadata.st_mode))

    def _resolve_segment(
        self,
        session_id: str,
        segment_id: str | None,
    ) -> tuple[ChangeSession, ChangeSessionSegment]:
        session = self._store.get_session(session_id)
        git_segments = tuple(
            item for item in session.segments if item.kind is ChangeSegmentKind.GIT_WORKTREE
        )
        if segment_id is None:
            if len(git_segments) != 1:
                raise ChangeSessionInvalidStateError(
                    "segment_id is required for composite sessions.",
                    session_id=session_id,
                )
            return session, git_segments[0]
        matches = tuple(item for item in git_segments if item.segment_id == segment_id)
        if not matches:
            raise ChangeSessionInvalidStateError(
                "Unknown or non-Git segment.",
                session_id=session_id,
                segment_id=segment_id,
            )
        return session, matches[0]

    def _require_active(self, session_id: str, segment_id: str) -> ChangeCheckpoint:
        active = self._store.get_active_checkpoint(session_id, segment_id)
        if active is None:
            raise ChangeCheckpointUnavailableError("restore")
        return active

    @staticmethod
    def _assert_expected(active: ChangeCheckpoint, expected: str | None) -> None:
        if expected is not None and expected != active.checkpoint_id:
            raise ChangeCheckpointStaleError(
                "Active checkpoint changed before the operation.",
                expected_checkpoint_id=expected,
                active_checkpoint_id=active.checkpoint_id,
            )

    def _mark_working(self, session: ChangeSession) -> None:
        now = datetime.now(UTC).isoformat()
        updated = ChangeSession(
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            workspace_root=session.workspace_root,
            topology_kind=session.topology_kind,
            status=ChangeSessionStatus.AGENT_WORKING,
            created_at=session.created_at,
            updated_at=now,
            expires_at=session.expires_at,
            segments=session.segments,
            candidate_digest=None,
            approval_id=None,
            warnings=session.warnings,
            git_details=tuple(
                type(item)(
                    repository_root=item.repository_root,
                    git_common_dir=item.git_common_dir,
                    target_branch=item.target_branch,
                    base_sha=item.base_sha,
                    temporary_branch=item.temporary_branch,
                    worktree_path=item.worktree_path,
                    candidate_commit=None,
                    integration_strategy=item.integration_strategy,
                    source_head_sha=item.source_head_sha,
                    baseline_commit=item.baseline_commit,
                    baseline_tree=item.baseline_tree,
                    timings_ms=item.timings_ms,
                )
                for item in session.git_details
            ),
            mirror_details=session.mirror_details,
            integration_failure=None,
        )
        self._store.save_session(updated)

    def _event(
        self,
        session_id: str,
        segment_id: str,
        event_type: str,
        checkpoint: ChangeCheckpoint,
    ) -> None:
        self._store.append_event(
            ChangeSessionEvent(
                event_type=event_type,
                occurred_at=datetime.now(UTC).isoformat(),
                session_id=session_id,
                segment_id=segment_id,
                details={
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "active_checkpoint_id": checkpoint.checkpoint_id,
                    "sequence": checkpoint.sequence,
                },
            )
        )

    @staticmethod
    def _result(
        checkpoint: ChangeCheckpoint,
        files: tuple[ChangeCheckpointFile, ...],
    ) -> ChangePatchResult:
        return ChangePatchResult(
            session_id=checkpoint.session_id,
            segment_id=checkpoint.segment_id,
            checkpoint_id=checkpoint.checkpoint_id,
            parent_checkpoint_id=checkpoint.parent_checkpoint_id,
            patch_sha256=checkpoint.patch_sha256,
            changed_files=tuple(dict.fromkeys(file.path for file in files)),
            active_checkpoint_id=checkpoint.checkpoint_id,
            status=ChangeSessionStatus.AGENT_WORKING.value,
        )


def _parse_patch(patch_text: str) -> tuple[_Operation, ...]:
    lines = patch_text.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ChangePatchInvalidError(
            "Patch must start with *** Begin Patch and end with *** End Patch."
        )
    operations: list[_Operation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ").strip()
            index += 1
            content: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                if not lines[index].startswith("+"):
                    raise ChangePatchInvalidError("Add File lines must start with '+'.", path=path)
                content.append(lines[index][1:])
                index += 1
            operations.append(_Operation("add", path, content=tuple(content)))
            continue
        if line.startswith("*** Delete File: "):
            operations.append(_Operation("delete", line.removeprefix("*** Delete File: ").strip()))
            index += 1
            continue
        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ").strip()
            index += 1
            move_to: str | None = None
            if index < len(lines) - 1 and lines[index].startswith("*** Move to: "):
                move_to = lines[index].removeprefix("*** Move to: ").strip()
                index += 1
            hunks: list[_Hunk] = []
            while index < len(lines) - 1 and lines[index].startswith("@@"):
                header = lines[index][2:].strip()
                index += 1
                hunk_lines: list[str] = []
                while (
                    index < len(lines) - 1
                    and not lines[index].startswith("@@")
                    and not lines[index].startswith("*** ")
                ):
                    if not lines[index] or lines[index][0] not in {" ", "+", "-"}:
                        raise ChangePatchInvalidError(
                            "Hunk lines must start with space, '+' or '-'."
                        )
                    hunk_lines.append(lines[index])
                    index += 1
                if index < len(lines) - 1 and lines[index] == "*** End of File":
                    index += 1
                hunks.append(_Hunk(header, tuple(hunk_lines)))
            if not hunks and move_to is None:
                raise ChangePatchInvalidError("Update File requires at least one hunk.", path=path)
            operations.append(_Operation("update", path, move_to=move_to, hunks=tuple(hunks)))
            continue
        raise ChangePatchInvalidError("Unexpected patch directive.", line=line)
    if not operations:
        raise ChangePatchInvalidError("Patch contains no file operations.")
    return tuple(operations)


def _apply_hunks(path: str, content: bytes, hunks: tuple[_Hunk, ...]) -> bytes:
    if b"\0" in content:
        raise ChangePatchBinaryUnsupportedError(path)
    bom = content.startswith(b"\xef\xbb\xbf")
    payload = content[3:] if bom else content
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ChangePatchBinaryUnsupportedError(path) from error
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    current = text.splitlines()
    cursor = 0
    for hunk in hunks:
        old = [line[1:] for line in hunk.lines if line[0] in {" ", "-"}]
        new = [line[1:] for line in hunk.lines if line[0] in {" ", "+"}]
        search_start = cursor
        if hunk.header:
            try:
                search_start = current.index(hunk.header, cursor)
            except ValueError:
                search_start = cursor
        if old:
            position = _find_sequence(current, old, search_start)
            if position is None:
                raise ChangePatchContextMismatchError(path)
        else:
            position = search_start if hunk.header else len(current)
        current[position : position + len(old)] = new
        cursor = position + len(new)
    rendered = newline.join(current)
    if trailing and current:
        rendered += newline
    encoded = rendered.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if bom else encoded


def _find_sequence(haystack: list[str], needle: list[str], start: int) -> int | None:
    limit = len(haystack) - len(needle) + 1
    for index in range(max(0, start), max(0, limit)):
        if haystack[index : index + len(needle)] == needle:
            return index
    return None


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False
