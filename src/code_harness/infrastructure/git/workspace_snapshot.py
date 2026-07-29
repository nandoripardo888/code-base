"""Compute workspace snapshots bound to a change set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import WorkspaceSnapshot


class GitWorkspaceSnapshotProvider:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve(strict=False)

    def snapshot_for_change_set(self, change_set: ChangeSet) -> WorkspaceSnapshot:
        file_digests: list[tuple[str, str]] = []
        for item in sorted(change_set.files, key=lambda file: file.path.casefold()):
            absolute = self._root / item.path
            if absolute.is_file():
                digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
            else:
                digest = "missing"
            file_digests.append((item.path.replace("\\", "/"), digest))
        payload = {
            "base_sha": change_set.base_sha,
            "change_set_id": change_set.change_set_id,
            "diff_sha256": change_set.diff_sha256,
            "files": dict(file_digests),
            "repository_id": change_set.repository_id,
        }
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return WorkspaceSnapshot(
            digest=digest,
            change_set_id=change_set.change_set_id,
            base_sha=change_set.base_sha,
            file_digests=tuple(file_digests),
        )
