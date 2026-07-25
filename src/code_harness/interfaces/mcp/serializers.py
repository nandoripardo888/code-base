"""Serialize application results for MCP tool responses."""

from code_harness.interfaces.response_projection import (
    ResponseDetail,
    resolve_response_detail,
    serialize_projected_result,
)
from code_harness.interfaces.serialization import (
    serialize_error,
    serialize_tool_result,
    to_primitive,
)

__all__ = [
    "ResponseDetail",
    "resolve_response_detail",
    "serialize_error",
    "serialize_projected_result",
    "serialize_tool_result",
    "to_primitive",
]
