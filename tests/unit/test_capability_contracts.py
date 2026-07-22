from datetime import UTC, datetime

from code_harness.domain.enums import CapabilityState, ErrorCode
from code_harness.domain.errors import (
    EmbeddingUnavailableError,
    PathOutsideProjectError,
    RipgrepUnavailableError,
    is_recoverable_error,
)
from code_harness.domain.models.capability import (
    CapabilityStatus,
    StrategyOutcome,
    ToolWarning,
)
from code_harness.domain.models.tool_result import ToolResult, warning_message
from code_harness.interfaces.serialization import serialize_error, serialize_tool_result, to_primitive


def test_capability_status_serializes_with_iso_timestamp() -> None:
    checked_at = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    status = CapabilityStatus(
        name="ripgrep",
        state=CapabilityState.UNAVAILABLE,
        optional=False,
        enabled=True,
        root_cause="Executable not found.",
        remediation="Install Ripgrep.",
        last_health_check=checked_at,
        metadata={"configured_executable": "rg"},
    )

    assert to_primitive(status) == {
        "name": "ripgrep",
        "state": "unavailable",
        "optional": False,
        "enabled": True,
        "root_cause": "Executable not found.",
        "remediation": "Install Ripgrep.",
        "last_health_check": checked_at.isoformat(),
        "metadata": {"configured_executable": "rg"},
    }


def test_strategy_outcome_and_tool_warning_round_trip() -> None:
    warning = ToolWarning(
        code="ripgrep_unavailable",
        message="Lexical expansion skipped.",
        recoverable=True,
        capability="ripgrep",
        remediation="Configure CODE_HARNESS_RG.",
    )
    outcome = StrategyOutcome(
        strategy="ripgrep",
        state=CapabilityState.UNAVAILABLE,
        hit_count=0,
        elapsed_ms=2,
        warning=warning,
        error_code="ripgrep_unavailable",
    )
    result = ToolResult(data=(), elapsed_ms=5, warnings=(warning,), strategies=(outcome,))

    payload = serialize_tool_result(result)
    assert payload["warnings"][0]["code"] == "ripgrep_unavailable"
    assert payload["strategies"][0]["state"] == "unavailable"
    assert warning_message(warning) == "Lexical expansion skipped."


def test_recoverable_errors_are_classified() -> None:
    recoverable = RipgrepUnavailableError("rg")
    fatal = PathOutsideProjectError("../x.py")
    semantic = EmbeddingUnavailableError("onnx missing")

    assert recoverable.recoverable is True
    assert recoverable.capability == "ripgrep"
    assert recoverable.remediation is not None
    assert is_recoverable_error(recoverable) is True

    assert fatal.recoverable is False
    assert is_recoverable_error(fatal) is False

    assert semantic.recoverable is True
    assert semantic.capability == "semantic"


def test_serialize_error_includes_recoverable_fields() -> None:
    error = RipgrepUnavailableError("rg")

    assert serialize_error(error) == {
        "error": {
            "code": ErrorCode.RIPGREP_UNAVAILABLE.value,
            "message": error.message,
            "details": {"executable": "rg"},
            "recoverable": True,
            "capability": "ripgrep",
            "remediation": error.remediation,
        }
    }
