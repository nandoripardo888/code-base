from dataclasses import replace

from code_harness.application.tools._timing import timed
from code_harness.domain.enums import CapabilityState, IndexState
from code_harness.domain.models.capability import CapabilityStatus
from code_harness.domain.models.index_report import IndexStatus
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.capability_reporter import CapabilityReporter
from code_harness.domain.protocols.repository_store import RepositoryStore


class GetIndexStatusTool:
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        semantic_model_id: str | None = None,
        capability_reporter: CapabilityReporter | None = None,
    ) -> None:
        self._project = project
        self._store = store
        self._semantic_model_id = semantic_model_id
        self._capability_reporter = capability_reporter

    def execute(self) -> ToolResult[IndexStatus]:
        status, elapsed_ms = timed(lambda: self._store.get_status(self._project))
        capabilities = (
            self._capability_reporter.report(status)
            if self._capability_reporter is not None
            else ()
        )
        service_state = _service_state(status, capabilities)
        status = replace(
            status,
            semantic_model_id=self._semantic_model_id,
            capabilities=capabilities,
            service_state=service_state,
        )
        return ToolResult(
            status,
            elapsed_ms,
            warnings=normalize_warnings(status.warnings, capability="index"),
            index_state=status.state.value,
        )


def _service_state(
    status: IndexStatus, capabilities: tuple[CapabilityStatus, ...]
) -> str:
    required_failed = [
        item
        for item in capabilities
        if not item.optional and item.state is CapabilityState.UNAVAILABLE
    ]
    if required_failed:
        return "degraded"
    optional_failed = [
        item
        for item in capabilities
        if item.optional and item.enabled and item.state is CapabilityState.UNAVAILABLE
    ]
    if optional_failed or status.state is IndexState.READY_WITH_WARNINGS:
        return IndexState.READY_WITH_WARNINGS.value
    if status.state is IndexState.READY:
        return IndexState.READY.value
    return status.state.value
