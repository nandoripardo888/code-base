"""Wire protocol for the native structural parser worker.

Three version axes stay separate on purpose:

* ``ANALYSIS_VERSION`` - structural result semantics; invalidates the index.
* ``PROTOCOL_VERSION`` - request/response envelope between supervisor and worker.
* ``WORKER_IMPLEMENTATION_VERSION`` - diagnostic only (transport/cache details).

Switching one-shot ? persistent transport must not bump ``ANALYSIS_VERSION``.
"""

from __future__ import annotations

from typing import Any

# Structural extraction / chunk semantics. Keep in sync with historical parser_version.
ANALYSIS_VERSION = "5"

# NDJSON envelope between supervisor and persistent worker.
PROTOCOL_VERSION = 1

# Diagnostic: persistent loop, Language/Parser cache, etc.
WORKER_IMPLEMENTATION_VERSION = "2"

OPERATION_ANALYZE = "analyze"
OPERATION_HEALTH = "health"
OPERATION_SHUTDOWN = "shutdown"

ERROR_KIND_ANALYSIS = "analysis_error"
ERROR_KIND_PROTOCOL = "protocol_error"


def build_request(
    *,
    request_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": operation,
        "payload": dict(payload or ()),
    }


def build_success_response(
    *,
    request_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


def build_error_response(
    *,
    request_id: str | None,
    kind: str,
    error_type: str,
    message: str,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": False,
        "error": {
            "kind": kind,
            "type": error_type,
            "message": message,
        },
    }


def validate_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("Parser request must be a JSON object.")
    version = request.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol_version {version!r}; expected {PROTOCOL_VERSION}.")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Parser request requires a non-empty request_id.")
    operation = request.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ValueError("Parser request requires an operation.")
    payload = request.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Parser request payload must be a JSON object.")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": operation,
        "payload": payload,
    }


def validate_response(response: Any, *, expected_request_id: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("Parser response must be a JSON object.")
    version = response.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol_version {version!r}; expected {PROTOCOL_VERSION}.")
    request_id = response.get("request_id")
    if request_id != expected_request_id:
        raise ValueError(f"Mismatched request_id {request_id!r}; expected {expected_request_id!r}.")
    if "ok" not in response:
        raise ValueError("Parser response is missing the ok field.")
    ok = response["ok"]
    if not isinstance(ok, bool):
        raise ValueError("Parser response ok must be a boolean.")
    if ok:
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("Successful parser response requires a result object.")
    else:
        error = response.get("error")
        if not isinstance(error, dict):
            raise ValueError("Failed parser response requires an error object.")
        for field in ("kind", "type", "message"):
            if field not in error:
                raise ValueError(f"Parser error object is missing {field}.")
    return response
