from __future__ import annotations

from code_harness.application.dto.review_requests import ListChangedFilesRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.change_set import ChangedFile
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider


class ListChangedFilesTool:
    def __init__(self, provider: ChangeProvider) -> None:
        self._provider = provider

    def execute(self, request: ListChangedFilesRequest) -> ToolResult[tuple[ChangedFile, ...]]:
        def list_files() -> tuple[ChangedFile, ...]:
            change_set = self._provider.get_change_set(request.change_set_id)
            return change_set.files

        files, elapsed_ms = timed(list_files)
        return ToolResult(files, elapsed_ms)
