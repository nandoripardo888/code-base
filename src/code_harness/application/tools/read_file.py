from code_harness.application.dto.requests import ReadFileRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.source_reader import SourceReader


class ReadFileTool:
    def __init__(self, reader: SourceReader) -> None:
        self._reader = reader

    def execute(self, request: ReadFileRequest) -> ToolResult[CodeSnippet]:
        outcome, elapsed_ms = timed(
            lambda: self._reader.read_file(
                request.path,
                max_chars=request.max_chars,
                max_lines=request.max_lines,
            )
        )
        return ToolResult(
            outcome.snippet,
            elapsed_ms,
            truncated=outcome.truncated,
            warnings=outcome.warnings,
        )
