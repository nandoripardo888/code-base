from __future__ import annotations

from code_harness.application.dto.review_requests import GetChangeSetRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ChangeSourceKind
from code_harness.domain.models.change_set import ChangeSet, ChangeSetRequest
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider


class GetChangeSetTool:
    def __init__(self, provider: ChangeProvider) -> None:
        self._provider = provider

    def execute(self, request: GetChangeSetRequest) -> ToolResult[ChangeSet]:
        change_set, elapsed_ms = timed(
            lambda: self._provider.create_change_set(
                ChangeSetRequest(
                    source=ChangeSourceKind(request.source),
                    base=request.base,
                    include_untracked=request.include_untracked,
                )
            )
        )
        return ToolResult(change_set, elapsed_ms)
