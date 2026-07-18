from code_harness.application.dto.requests import ListFilesRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.file_catalog import FileCatalog


class ListFilesTool:
    def __init__(self, catalog: FileCatalog) -> None:
        self._catalog = catalog

    def execute(self, request: ListFilesRequest) -> ToolResult[tuple[SourceFile, ...]]:
        files, elapsed_ms = timed(
            lambda: self._catalog.list_files(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
            )
        )
        truncated = len(files) > request.max_results
        return ToolResult(files[: request.max_results], elapsed_ms, truncated=truncated)
