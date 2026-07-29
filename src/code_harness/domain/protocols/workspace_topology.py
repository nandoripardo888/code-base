from __future__ import annotations

from typing import Protocol

from code_harness.domain.models.workspace_topology import WorkspaceTopology


class WorkspaceTopologyResolver(Protocol):
    def resolve(self, workspace_root: str) -> WorkspaceTopology: ...
