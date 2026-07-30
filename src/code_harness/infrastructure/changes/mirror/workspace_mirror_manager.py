from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from code_harness.domain.enums import FileChangeKind
from code_harness.domain.errors import ChangeSessionPathRejectedError
from code_harness.domain.models.change_segment import MirrorChangeSegment
from code_harness.domain.models.change_set import ChangedFile
from code_harness.domain.models.workspace_manifest import (
    MirrorPrepareResult,
    ProposedFileChange,
    WorkspaceManifestEntry,
)
from code_harness.domain.protocols.blob_store import BlobStore
from code_harness.infrastructure.changes.git.worktree_manager import assert_under_sessions_home

_SKIP_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".code-harness",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "venv",
    }
)


class WorkspaceMirrorManager:
    def __init__(self, *, blob_store: BlobStore, sessions_home: Path | None = None) -> None:
        self._blobs = blob_store
        self._sessions_home = sessions_home

    def create(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        sessions_home: Path,
        include_relative_roots: tuple[str, ...] | None = None,
        exclude_git_roots: tuple[str, ...] = (),
    ) -> MirrorChangeSegment:
        source = Path(source_root).resolve(strict=False)
        mirror = assert_under_sessions_home(
            sessions_home / session_id / "mirrors" / segment_id / "workspace",
            sessions_home,
        )
        if mirror.exists():
            raise ChangeSessionPathRejectedError(str(mirror), "mirror path already exists")
        mirror.mkdir(parents=True, exist_ok=True)
        excluded = {Path(item).resolve(strict=False) for item in exclude_git_roots if item}
        self._copy_tree(
            source,
            mirror,
            include_relative_roots=include_relative_roots,
            exclude_roots=excluded,
        )
        entries = self._build_manifest(
            mirror,
            ref_owner=session_id,
            ref_kind=f"base:{segment_id}",
        )
        digest = _manifest_digest(entries)
        return MirrorChangeSegment(
            source_root=str(source),
            mirror_root=str(mirror),
            base_manifest_digest=digest,
            candidate_manifest_digest=None,
            changed_files=(),
        )

    def create_with_manifest(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        sessions_home: Path,
        include_relative_roots: tuple[str, ...] | None = None,
        exclude_git_roots: tuple[str, ...] = (),
    ) -> MirrorChangeSegment:
        return self.create(
            session_id=session_id,
            segment_id=segment_id,
            source_root=source_root,
            sessions_home=sessions_home,
            include_relative_roots=include_relative_roots,
            exclude_git_roots=exclude_git_roots,
        )

    def prepare(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        mirror_root: Path,
        base_manifest_digest: str,
        sessions_home: Path | None = None,
    ) -> MirrorPrepareResult:
        return self._compare(
            session_id=session_id,
            segment_id=segment_id,
            source_root=source_root,
            mirror_root=mirror_root,
            base_manifest_digest=base_manifest_digest,
            sessions_home=sessions_home,
            persist_blobs=True,
            allow_empty=False,
        )

    def preview(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        mirror_root: Path,
        base_manifest_digest: str,
        sessions_home: Path | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        result = self._compare(
            session_id=session_id,
            segment_id=segment_id,
            source_root=source_root,
            mirror_root=mirror_root,
            base_manifest_digest=base_manifest_digest,
            sessions_home=sessions_home,
            persist_blobs=False,
            allow_empty=True,
        )
        return result.unified_text, result.files

    def _compare(
        self,
        *,
        session_id: str,
        segment_id: str,
        source_root: Path,
        mirror_root: Path,
        base_manifest_digest: str,
        sessions_home: Path | None,
        persist_blobs: bool,
        allow_empty: bool,
    ) -> MirrorPrepareResult:
        mirror = Path(mirror_root).resolve(strict=False)
        home = sessions_home or self._sessions_home
        if home is not None:
            assert_under_sessions_home(mirror, home)
        current = self._scan_entries(mirror)
        manifest_path = mirror.parent / "base-manifest.json"
        if not manifest_path.is_file():
            raise ChangeSessionPathRejectedError(
                str(manifest_path),
                "base manifest missing for mirror segment",
            )
        base_entries = {
            item["path"]: WorkspaceManifestEntry(
                path=item["path"],
                content_sha256=item["content_sha256"],
                size_bytes=item["size_bytes"],
                modified_at_ns=item["modified_at_ns"],
                kind=item["kind"],
                blob_id=item.get("blob_id"),
            )
            for item in json.loads(manifest_path.read_text(encoding="utf-8"))
        }
        proposed: list[ProposedFileChange] = []
        changed_files: list[ChangedFile] = []
        current_map = {entry.path: entry for entry in current}
        for path, base in base_entries.items():
            cur = current_map.get(path)
            if cur is None:
                proposed.append(
                    ProposedFileChange(
                        path=path,
                        operation="deleted",
                        base_sha256=base.content_sha256,
                        proposed_sha256=None,
                        base_blob_id=base.blob_id,
                        proposed_blob_id=None,
                    )
                )
                changed_files.append(
                    ChangedFile(
                        path=path,
                        kind=FileChangeKind.DELETED,
                        old_sha256=base.content_sha256,
                    )
                )
            elif cur.content_sha256 != base.content_sha256:
                blob_id = (
                    self._blobs.put(
                        (mirror / path).read_bytes(),
                        ref_owner=session_id,
                        ref_kind=f"proposed:{segment_id}:{path}",
                    )
                    if persist_blobs
                    else None
                )
                proposed.append(
                    ProposedFileChange(
                        path=path,
                        operation="modified",
                        base_sha256=base.content_sha256,
                        proposed_sha256=cur.content_sha256,
                        base_blob_id=base.blob_id,
                        proposed_blob_id=blob_id,
                    )
                )
                kind = FileChangeKind.BINARY if cur.kind == "binary" else FileChangeKind.MODIFIED
                changed_files.append(
                    ChangedFile(
                        path=path,
                        kind=kind,
                        binary=cur.kind == "binary",
                        old_sha256=base.content_sha256,
                        new_sha256=cur.content_sha256,
                    )
                )
        for path, cur in current_map.items():
            if path in base_entries:
                continue
            blob_id = (
                self._blobs.put(
                    (mirror / path).read_bytes(),
                    ref_owner=session_id,
                    ref_kind=f"proposed:{segment_id}:{path}",
                )
                if persist_blobs
                else None
            )
            proposed.append(
                ProposedFileChange(
                    path=path,
                    operation="added",
                    base_sha256=None,
                    proposed_sha256=cur.content_sha256,
                    base_blob_id=None,
                    proposed_blob_id=blob_id,
                )
            )
            kind = FileChangeKind.BINARY if cur.kind == "binary" else FileChangeKind.ADDED
            changed_files.append(
                ChangedFile(
                    path=path,
                    kind=kind,
                    binary=cur.kind == "binary",
                    new_sha256=cur.content_sha256,
                )
            )
        if not proposed and not allow_empty:
            raise ChangeSessionPathRejectedError(
                str(mirror),
                "No changes to prepare in the isolated mirror",
            )
        ordered = tuple(sorted(proposed, key=lambda item: item.path.casefold()))
        files = tuple(item.path for item in ordered)
        candidate_manifest = _manifest_digest(tuple(current_map.values()))
        unified = _derive_unified_text(ordered, mirror)
        digest_payload = "|".join(
            (
                session_id,
                segment_id,
                str(source_root),
                base_manifest_digest,
                candidate_manifest,
                ",".join(files),
                hashlib.sha256(unified.encode("utf-8", errors="replace")).hexdigest(),
            )
        )
        candidate_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        detail = MirrorChangeSegment(
            source_root=str(Path(source_root).resolve(strict=False)),
            mirror_root=str(mirror),
            base_manifest_digest=base_manifest_digest,
            candidate_manifest_digest=candidate_manifest,
            changed_files=tuple(changed_files),
        )
        return MirrorPrepareResult(
            detail=detail,
            proposed_changes=ordered,
            unified_text=unified,
            files=files,
            candidate_digest=candidate_digest,
        )

    def write_base_manifest(
        self,
        mirror_root: Path,
        entries: tuple[WorkspaceManifestEntry, ...],
    ) -> None:
        target = Path(mirror_root).parent / "base-manifest.json"
        payload = [
            {
                "path": item.path,
                "content_sha256": item.content_sha256,
                "size_bytes": item.size_bytes,
                "modified_at_ns": item.modified_at_ns,
                "kind": item.kind,
                "blob_id": item.blob_id,
            }
            for item in entries
        ]
        target.write_text(json.dumps(payload), encoding="utf-8")

    def _copy_tree(
        self,
        source: Path,
        dest: Path,
        *,
        include_relative_roots: tuple[str, ...] | None,
        exclude_roots: set[Path],
    ) -> None:
        if include_relative_roots is None or include_relative_roots == (".",):
            self._copy_filtered(source, dest, exclude_roots=exclude_roots)
            return
        for relative in include_relative_roots:
            if relative in {".", "./"}:
                self._copy_filtered(source, dest, exclude_roots=exclude_roots)
                continue
            src_item = source / relative
            dst_item = dest / relative
            if not src_item.exists():
                continue
            if src_item.is_file():
                dst_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_item, dst_item)
            else:
                dst_item.mkdir(parents=True, exist_ok=True)
                self._copy_filtered(src_item, dst_item, exclude_roots=exclude_roots)

    def _copy_filtered(
        self,
        source: Path,
        dest: Path,
        *,
        exclude_roots: set[Path],
    ) -> None:
        for root, dirs, files in os.walk(source):
            root_path = Path(root)
            resolved = root_path.resolve(strict=False)
            if resolved in exclude_roots or any(
                resolved == excluded or excluded in resolved.parents for excluded in exclude_roots
            ):
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name not in _SKIP_NAMES]
            rel_root = root_path.relative_to(source).as_posix()
            target_root = dest if rel_root == "." else dest / rel_root
            target_root.mkdir(parents=True, exist_ok=True)
            for name in files:
                if name in _SKIP_NAMES:
                    continue
                src_file = root_path / name
                if src_file.is_symlink():
                    continue
                shutil.copy2(src_file, target_root / name)

    def _build_manifest(
        self,
        root: Path,
        *,
        ref_owner: str,
        ref_kind: str,
    ) -> tuple[WorkspaceManifestEntry, ...]:
        entries = self._scan_entries(root)
        stored: list[WorkspaceManifestEntry] = []
        for entry in entries:
            content = (root / entry.path).read_bytes()
            blob_id = self._blobs.put(
                content,
                ref_owner=ref_owner,
                ref_kind=f"{ref_kind}:{entry.path}",
            )
            stored.append(
                WorkspaceManifestEntry(
                    path=entry.path,
                    content_sha256=entry.content_sha256,
                    size_bytes=entry.size_bytes,
                    modified_at_ns=entry.modified_at_ns,
                    kind=entry.kind,
                    blob_id=blob_id,
                )
            )
        self.write_base_manifest(root, tuple(stored))
        return tuple(stored)

    def _scan_entries(self, root: Path) -> tuple[WorkspaceManifestEntry, ...]:
        entries: list[WorkspaceManifestEntry] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in _SKIP_NAMES for part in path.parts):
                continue
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            try:
                data.decode("utf-8")
                kind = "file"
            except UnicodeDecodeError:
                kind = "binary"
            stat = path.stat()
            entries.append(
                WorkspaceManifestEntry(
                    path=relative,
                    content_sha256=digest,
                    size_bytes=stat.st_size,
                    modified_at_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
                    kind=kind,
                    blob_id=None,
                )
            )
        return tuple(entries)


def _manifest_digest(entries: tuple[WorkspaceManifestEntry, ...]) -> str:
    lines = [
        f"{item.path}:{item.content_sha256}:{item.size_bytes}:{item.kind}"
        for item in sorted(entries, key=lambda entry: entry.path.casefold())
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _derive_unified_text(changes: tuple[ProposedFileChange, ...], mirror: Path) -> str:
    chunks: list[str] = []
    for change in changes:
        chunks.append(f"--- {change.path} ({change.operation})")
        if change.operation == "deleted":
            chunks.append(f"base={change.base_sha256}")
            continue
        if change.operation == "added":
            chunks.append(f"proposed={change.proposed_sha256}")
            try:
                text = (mirror / change.path).read_text(encoding="utf-8")
                chunks.extend(f"+{line}" for line in text.splitlines())
            except (OSError, UnicodeDecodeError):
                chunks.append("<binary or unreadable>")
            continue
        chunks.append(f"base={change.base_sha256} proposed={change.proposed_sha256}")
        try:
            text = (mirror / change.path).read_text(encoding="utf-8")
            chunks.extend(f"+{line}" for line in text.splitlines()[:200])
        except (OSError, UnicodeDecodeError):
            chunks.append("<binary or unreadable>")
    return "\n".join(chunks)
