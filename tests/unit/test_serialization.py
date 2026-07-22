from code_harness.domain.enums import ErrorCode
from code_harness.domain.errors import PathOutsideProjectError
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.serialization import (
    serialize_error,
    serialize_tool_result,
    to_primitive,
)


def test_to_primitive_converts_nested_dataclasses_and_enums() -> None:
    result = ToolResult(data=("a", "b"), elapsed_ms=12, warnings=("w",), index_state="ready")

    assert to_primitive(result) == {
        "data": ["a", "b"],
        "elapsed_ms": 12,
        "truncated": False,
        "warnings": ["w"],
        "index_state": "ready",
    }


def test_serialize_error_matches_cli_envelope() -> None:
    error = PathOutsideProjectError("../outside.py")

    assert serialize_error(error) == {
        "error": {
            "code": ErrorCode.PATH_OUTSIDE_PROJECT.value,
            "message": error.message,
            "details": error.details,
            "recoverable": False,
        }
    }


def test_serialize_tool_result_omits_empty_strategies() -> None:
    result = ToolResult(data={"ok": True}, elapsed_ms=1)

    assert serialize_tool_result(result) == {
        "data": {"ok": True},
        "elapsed_ms": 1,
        "truncated": False,
        "warnings": [],
        "index_state": None,
    }
