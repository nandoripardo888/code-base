from dataclasses import replace

from code_harness.application.tools._timing import timed
from code_harness.domain.models.index_report import IndexStatus
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.repository_store import RepositoryStore


class GetIndexStatusTool:
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        semantic_model_id: str | None = None,
    ) -> None:
        self._project = project
        self._store = store
        self._semantic_model_id = semantic_model_id

    def execute(self) -> ToolResult[IndexStatus]:
        status, elapsed_ms = timed(lambda: self._store.get_status(self._project))
        status = replace(status, semantic_model_id=self._semantic_model_id)
        return ToolResult(
            status,
            elapsed_ms,
            warnings=status.warnings,
            index_state=status.state.value,
        )
