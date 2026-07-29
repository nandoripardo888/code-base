from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from code_harness.domain.errors import (
    ChangeSessionPathRejectedError,
    ChangeSessionStaleError,
)
from code_harness.domain.models.change_segment import MirrorChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange
from code_harness.domain.protocols.blob_store import BlobStore
from code_harness.infrastructure.changes.git.worktree_manager import assert_under_sessions_home


class MirrorChangeIntegrator:
    def __init__(self, *, blob_store: BlobStore, sessions_home: Path) -> None:
        self._blobs = blob_store
        self._sessions_home = Path(sessions_home)

    def preflight_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None:
        source = Path(detail.source_root).resolve(strict=False)
        for change in proposed:
            target = source / change.path
            if change.operation == "added":
                if target.exists():
                    raise ChangeSessionStaleError(
                        f"Proposed new file already exists in workspace: {change.path}",
                        path=change.path,
                    )
                continue
            if not target.is_file():
                raise ChangeSessionStaleError(
                    f"Expected file missing in workspace: {change.path}",
                    path=change.path,
                )
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current != change.base_sha256:
                raise ChangeSessionStaleError(
                    f"Workspace file changed since session base: {change.path}",
                    path=change.path,
                    expected_sha256=change.base_sha256,
                    actual_sha256=current,
                )

    def integrate_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
        journal_path: Path | None = None,
    ) -> None:
        self.preflight_mirror(detail, proposed)
        source = Path(detail.source_root).resolve(strict=False)
        assert_under_sessions_home(Path(detail.mirror_root), self._sessions_home)
        rollback_root = (
            Path(detail.mirror_root).parent / "rollback"
        )
        rollback_root.mkdir(parents=True, exist_ok=True)
        journal = journal_path or (Path(detail.mirror_root).parent / "apply-journal.jsonl")
        applied: list[dict[str, object]] = []
        try:
            for change in proposed:
                target = source / change.path
                record: dict[str, object] = {
                    "path": change.path,
                    "operation": change.operation,
                }
                if change.operation == "deleted":
                    backup = rollback_root / change.path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(backup))
                    record["backup"] = str(backup)
                    applied.append(record)
                    _append_journal(journal, record)
                    continue
                assert change.proposed_blob_id is not None
                content = self._blobs.get(change.proposed_blob_id)
                digest = hashlib.sha256(content).hexdigest()
                if digest != change.proposed_sha256:
                    raise ChangeSessionStaleError(
                        f"Proposed blob digest mismatch for {change.path}",
                        path=change.path,
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(f".{target.name}.code-harness-tmp")
                if change.operation == "modified" and target.exists():
                    backup = rollback_root / change.path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    record["backup"] = str(backup)
                tmp.write_bytes(content)
                try:
                    with tmp.open("rb") as handle:
                        handle.flush()
                        try:
                            os.fsync(handle.fileno())
                        except OSError:
                            pass
                except OSError:
                    pass
                written = hashlib.sha256(tmp.read_bytes()).hexdigest()
                if written != change.proposed_sha256:
                    tmp.unlink(missing_ok=True)
                    raise ChangeSessionStaleError(
                        f"Written content digest mismatch for {change.path}",
                        path=change.path,
                    )
                os.replace(tmp, target)
                record["applied"] = True
                applied.append(record)
                _append_journal(journal, record)
            # Success: remove rollback copies.
            if rollback_root.exists():
                shutil.rmtree(rollback_root, ignore_errors=True)
        except Exception:
            self._rollback(source, applied)
            raise

    def _rollback(self, source: Path, applied: list[dict[str, object]]) -> None:
        for record in reversed(applied):
            path = str(record["path"])
            operation = str(record["operation"])
            target = source / path
            backup = record.get("backup")
            try:
                if operation == "added":
                    target.unlink(missing_ok=True)
                elif isinstance(backup, str) and Path(backup).exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        target.unlink()
                    shutil.move(backup, str(target))
            except OSError:
                continue


def _append_journal(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
