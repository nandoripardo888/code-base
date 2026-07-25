from code_harness.domain.enums import ErrorCode
from code_harness.domain.errors import InternalToolError, PathOutsideProjectError
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


def test_internal_error_is_correlated_without_leaking_exception_details() -> None:
    payload = serialize_error(InternalToolError("build_context", "error-123"))

    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["details"] == {
        "error_id": "error-123",
        "tool": "build_context",
    }
    assert "list index" not in str(payload)


def test_serialize_tool_result_omits_empty_strategies() -> None:
    result = ToolResult(data={"ok": True}, elapsed_ms=1)

    assert serialize_tool_result(result) == {
        "data": {"ok": True},
        "elapsed_ms": 1,
        "truncated": False,
        "warnings": [],
        "index_state": None,
    }


def test_serialize_command_inspection() -> None:
    from code_harness.domain.enums import CommandKind, PolicyDecision
    from code_harness.domain.models.execution import ApprovalDigest, CommandInspection

    inspection = CommandInspection(
        kind=CommandKind.PROCESS,
        decision=PolicyDecision.ALLOW,
        requested_capabilities=(),
        required_capabilities=(),
        approval_required=False,
        reasons=(),
        risks=(),
        blocks=(),
        approval_digest=ApprovalDigest(value="abc"),
        cwd=".",
        timeout_seconds=60.0,
        max_output_bytes=100,
        executable="git",
        args=("status",),
    )
    payload = serialize_tool_result(ToolResult(inspection, elapsed_ms=3))
    assert payload["data"]["decision"] == "allow"
    assert payload["data"]["approval_digest"]["value"] == "abc"
    assert payload["data"]["executable"] == "git"
