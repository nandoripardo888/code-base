from types import SimpleNamespace
from typing import Any, cast

from code_harness.domain.errors import ChangeSessionUnsupportedTopologyError
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.mcp.change_handlers import _execute_change_operation


class _FakeContainer:
    def with_index_state(self, result: object) -> object:
        return result


def test_change_handlers_serialize_typed_errors_without_detail_arg() -> None:
    def fail() -> ToolResult[Any]:
        raise ChangeSessionUnsupportedTopologyError("in_place")

    payload = _execute_change_operation(
        cast(Any, _FakeContainer()),
        fail,
        "compact",
    )

    assert payload["error"]["code"] == "change_session_unsupported_topology"
    assert payload["error"]["details"]["topology_kind"] == "in_place"
    assert "data" not in payload


def test_change_handlers_serialize_unexpected_errors() -> None:
    def fail() -> ToolResult[Any]:
        raise RuntimeError("secret traceback")

    payload = _execute_change_operation(
        cast(Any, SimpleNamespace(with_index_state=lambda result: result)),
        fail,
        None,
    )

    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["details"]["tool"] == "change_session"
    assert payload["error"]["details"]["error_id"]
    assert "secret traceback" not in str(payload)
