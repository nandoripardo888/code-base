from code_harness.application.dto.requests import ReadRangeRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.models.code_chunk import SourceRead
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.source_reader import SourceReader


class ReadRangeTool:
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

    def execute(self, request: ReadRangeRequest) -> ToolResult[SourceRead]:
        outcome, elapsed_ms = timed(
            lambda: self._reader.read_range(
                request.path,
                start_line=request.start_line,
                end_line=request.end_line,
                max_chars=request.max_chars,
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
