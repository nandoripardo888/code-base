from code_harness.application.dto.requests import ReadRangeRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.source_reader import SourceReader


class ReadRangeTool:
    def __init__(self, reader: SourceReader) -> None:
        self._reader = reader

    def execute(self, request: ReadRangeRequest) -> ToolResult[CodeSnippet]:
        outcome, elapsed_ms = timed(
            lambda: self._reader.read_range(
                request.path,
                start_line=request.start_line,
                end_line=request.end_line,
                max_chars=request.max_chars,
            )
        )
        return ToolResult(
            outcome.snippet,
            elapsed_ms,
            truncated=outcome.truncated,
            warnings=outcome.warnings,
        )
