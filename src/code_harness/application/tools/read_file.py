from code_harness.application.dto.requests import ReadFileRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.models.code_chunk import SourceRead
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.source_reader import SourceReader


class ReadFileTool:
    def __init__(
        self,
        reader: SourceReader,
        *,
        project: Project | None = None,
        store: RepositoryStore | None = None,
    ) -> None:
        self._reader = reader
        self._project = project
        self._store = store

    def execute(self, request: ReadFileRequest) -> ToolResult[SourceRead]:
        outcome, elapsed_ms = timed(
            lambda: self._reader.read_file(
                request.path,
                max_chars=request.max_chars,
                max_lines=request.max_lines,
                include_line_numbers=request.include_line_numbers,
            )
        )
        return ToolResult(
            outcome,
            elapsed_ms,
            truncated=outcome.truncated,
            warnings=normalize_warnings(outcome.warnings, capability="filesystem"),
            index_state=resolve_index_state(self._store, self._project),
        )
