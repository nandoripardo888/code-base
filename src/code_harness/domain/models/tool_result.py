from dataclasses import dataclass, field

from code_harness.domain.models.capability import StrategyOutcome, ToolWarning


@dataclass(frozen=True, slots=True)
class ToolResult[T]:
    data: T
    elapsed_ms: int
    truncated: bool = False
    warnings: tuple[ToolWarning, ...] = ()
    index_state: str | None = None
    strategies: tuple[StrategyOutcome, ...] = field(default_factory=tuple)


def warning_message(warning: str | ToolWarning) -> str:
    return warning.message if isinstance(warning, ToolWarning) else warning


def as_tool_warning(
    warning: str | ToolWarning,
    *,
    code: str = "tool_warning",
    capability: str | None = None,
    recoverable: bool = True,
    remediation: str | None = None,
) -> ToolWarning:
    if isinstance(warning, ToolWarning):
        return warning
    return ToolWarning(
        code=code,
        message=warning,
        recoverable=recoverable,
        capability=capability,
        remediation=remediation,
    )


def normalize_warnings(
    warnings: tuple[str | ToolWarning, ...] | list[str | ToolWarning],
    *,
    code: str = "tool_warning",
    capability: str | None = None,
) -> tuple[ToolWarning, ...]:
    return tuple(
        dict.fromkeys(
            as_tool_warning(warning, code=code, capability=capability) for warning in warnings
        )
    )
