from __future__ import annotations

from pathlib import Path

from code_harness.domain.models.change_segment import GitChangeSegment, MirrorChangeSegment
from code_harness.domain.models.workspace_manifest import ProposedFileChange
from code_harness.infrastructure.changes.git.git_integrator import GitChangeIntegrator
from code_harness.infrastructure.changes.mirror.mirror_integrator import MirrorChangeIntegrator


class CombinedChangeIntegrator:
    def __init__(
        self,
        *,
        git: GitChangeIntegrator | None = None,
        mirror: MirrorChangeIntegrator | None = None,
    ) -> None:
        self._git = git or GitChangeIntegrator()
        self._mirror = mirror

    def preflight_git(self, detail: GitChangeSegment) -> None:
        self._git.preflight_git(detail)

    def integrate_git(self, detail: GitChangeSegment) -> str:
        return self._git.integrate_git(detail)

    def preflight_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
    ) -> None:
        if self._mirror is None:
            raise RuntimeError("Mirror integrator is not configured.")
        self._mirror.preflight_mirror(detail, proposed)

    def integrate_mirror(
        self,
        detail: MirrorChangeSegment,
        proposed: tuple[ProposedFileChange, ...],
        *,
        session_id: str,
    ) -> None:
        if self._mirror is None:
            raise RuntimeError("Mirror integrator is not configured.")
        self._mirror.integrate_mirror(detail, proposed, session_id=session_id)
