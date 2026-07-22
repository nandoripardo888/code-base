"""Helpers for resolving index_state consistently across tools."""

from __future__ import annotations

from code_harness.domain.enums import IndexState
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.project import Project
from code_harness.domain.protocols.repository_store import RepositoryStore


def resolve_index_state(store: RepositoryStore | None, project: Project | None) -> str | None:
    """Return the current index state using the shared contract.

    Rules:
    - unknown store ? ``None``
    - known store without index ? ``not_initialized``
    - existing index ? current state value
    """
    if store is None or project is None:
        return None
    try:
        status = store.get_status(project)
    except CodeHarnessError:
        return IndexState.NOT_INITIALIZED.value
    except Exception:
        return None
    return status.state.value
