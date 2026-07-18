from dataclasses import FrozenInstanceError

import pytest

from code_harness.application.dto.requests import (
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchFilesRequest,
    SearchTextRequest,
)
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.tool_result import ToolResult


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("", 1, 1), "path"),
        (("file.py", 0, 1), "start_line"),
        (("file.py", 2, 1), "end_line"),
        (("file.py", 1, 1, 0), "start_column"),
    ],
)
def test_code_location_validates_invariants(arguments: tuple[object, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CodeLocation(*arguments)  # type: ignore[arg-type]


def test_code_location_and_tool_result_are_immutable() -> None:
    location = CodeLocation("src/file.py", 1, 2)
    result = ToolResult(location, 3)

    with pytest.raises(FrozenInstanceError):
        location.start_line = 4  # type: ignore[misc]
    assert result.data is location


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ListFilesRequest(max_results=0),
        lambda: SearchFilesRequest(""),
        lambda: SearchTextRequest("needle", context_lines=-1),
        lambda: SearchTextRequest("needle", timeout_seconds=0),
        lambda: ReadFileRequest(""),
        lambda: ReadRangeRequest("x", 2, 1),
    ],
)
def test_requests_reject_invalid_limits(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]
