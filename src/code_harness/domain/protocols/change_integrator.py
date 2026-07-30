from __future__ import annotations

from typing import Protocol

from code_harness.domain.models.change_segment import GitChangeSegment, MirrorChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange


class ChangeIntegrator(Protocol):
    def preflight_git(self, detail: GitChangeSegment) -> None: ...

    def integrate_git(self, detail: GitChangeSegment) -> str:
        """Cherry-pick candidate; return resulting HEAD sha. Raises on conflict."""
        ...

    def preflight_workspace_patch(
        self,
        detail: GitChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None: ...

    def integrate_workspace_patch(
        self,
        detail: GitChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
    ) -> None: ...

    def preflight_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None: ...

    def integrate_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
    ) -> None: ...
