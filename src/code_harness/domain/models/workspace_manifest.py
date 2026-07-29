from __future__ import annotations

from dataclasses import dataclass

from code_harness.domain.models.change_segment import MirrorChangeSegment


@dataclass(frozen=True, slots=True)
class WorkspaceManifestEntry:
    path: str
    content_sha256: str
    size_bytes: int
    modified_at_ns: int
    kind: str
    blob_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedFileChange:
    path: str
    operation: str
    base_sha256: str | None
    proposed_sha256: str | None
    base_blob_id: str | None
    proposed_blob_id: str | None


@dataclass(frozen=True, slots=True)
class MirrorPrepareResult:
    detail: MirrorChangeSegment
    proposed_changes: tuple[ProposedFileChange, ...]
    unified_text: str
    files: tuple[str, ...]
    candidate_digest: str
