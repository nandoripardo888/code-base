from code_harness.infrastructure.parsers.native_protocol import (
    ANALYSIS_VERSION,
    PROTOCOL_VERSION,
    WORKER_IMPLEMENTATION_VERSION,
    build_error_response,
    build_request,
    build_success_response,
    validate_request,
    validate_response,
)


def test_version_axes_are_separated() -> None:
    assert ANALYSIS_VERSION == "4"
    assert PROTOCOL_VERSION == 1
    assert WORKER_IMPLEMENTATION_VERSION == "2"
    assert ANALYSIS_VERSION != WORKER_IMPLEMENTATION_VERSION


def test_validate_request_and_response_round_trip() -> None:
    request = build_request(
        request_id="worker-0-000001",
        operation="analyze",
        payload={"path": "a.py", "language": "python", "content": "x=1\n"},
    )
    validated = validate_request(request)
    assert validated["request_id"] == "worker-0-000001"

    response = build_success_response(
        request_id="worker-0-000001",
        result={"parser_name": "x", "parser_version": ANALYSIS_VERSION, "state": "ready"},
    )
    assert validate_response(response, expected_request_id="worker-0-000001")["ok"] is True

    error = build_error_response(
        request_id="worker-0-000001",
        kind="analysis_error",
        error_type="ValueError",
        message="boom",
    )
    assert validate_response(error, expected_request_id="worker-0-000001")["ok"] is False
