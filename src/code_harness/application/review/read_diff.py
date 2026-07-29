from __future__ import annotations

from code_harness.application.dto.review_requests import ReadDiffRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.errors import InvalidChangeRequestError
from code_harness.domain.models.change_set import ChangedFile, ChangeDiff
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.change_provider import ChangeProvider


class ReadDiffTool:
    def __init__(self, provider: ChangeProvider) -> None:
        self._provider = provider

    def execute(self, request: ReadDiffRequest) -> ToolResult[ChangeDiff]:
        def read() -> ChangeDiff:
            change_set = self._provider.get_change_set(request.change_set_id)
            diff = self._provider.read_diff(change_set)
            if request.path is None:
                return diff
            matched = tuple(
                item
                for item in diff.files
                if item.path == request.path or item.old_path == request.path
            )
            if not matched:
                raise InvalidChangeRequestError(
                    "Path was not found in the change set.",
                    path=request.path,
                    change_set_id=request.change_set_id,
                )
            return ChangeDiff(
                change_set_id=diff.change_set_id,
                files=matched,
                unified_text=_filter_unified(diff.unified_text, matched),
            )

        result, elapsed_ms = timed(read)
        return ToolResult(result, elapsed_ms)


def _filter_unified(text: str, files: tuple[ChangedFile, ...]) -> str:
    if not text:
        return text
    wanted = {item.path for item in files} | {
        item.old_path for item in files if item.old_path is not None
    }
    chunks: list[str] = []
    current: list[str] = []
    keep = False
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if keep and current:
                chunks.extend(current)
            current = [line]
            keep = any(f"a/{path}" in line or f"b/{path}" in line for path in wanted)
            continue
        if current:
            current.append(line)
    if keep and current:
        chunks.extend(current)
    return "".join(chunks)
