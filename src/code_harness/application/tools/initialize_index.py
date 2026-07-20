from code_harness.application.tools._timing import timed
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.repository_store import RepositoryStore


class InitializeIndexTool:
    def __init__(self, project: Project, store: RepositoryStore) -> None:
        self._project = project
        self._store = store

    def execute(self) -> ToolResult[Project]:
        _, elapsed_ms = timed(lambda: self._store.initialize(self._project))
        return ToolResult(self._project, elapsed_ms, index_state="not_initialized")
