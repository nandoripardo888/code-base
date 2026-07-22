"""Shared structured serialization for CLI and MCP adapters."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.tool_result import ToolResult


def to_primitive(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        payload = {
            field.name: to_primitive(getattr(value, field.name)) for field in fields(value)
        }
        if isinstance(value, ToolWarning):
            return {key: item for key, item in payload.items() if item is not None}
        from code_harness.domain.models.search_hit import SearchHit
        from code_harness.domain.models.structural import StructuralSearchResult

        if isinstance(value, SearchHit) and payload.get("evidence") is None:
            payload.pop("evidence", None)
        if isinstance(value, StructuralSearchResult) and not value.content_included:
            payload.pop("content", None)
        if isinstance(value, ToolResult) and not payload.get("strategies"):
            payload.pop("strategies", None)
        return payload
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    return value


def serialize_tool_result(result: ToolResult[Any]) -> dict[str, Any]:
    payload = to_primitive(result)
    assert isinstance(payload, dict)
    return payload


def serialize_error(error: CodeHarnessError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": error.code.value,
        "message": error.message,
        "details": to_primitive(error.details),
        "recoverable": error.recoverable,
    }
    if error.capability is not None:
        payload["capability"] = error.capability
    if error.remediation is not None:
        payload["remediation"] = error.remediation
    return {"error": payload}
