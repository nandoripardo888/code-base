from collections.abc import Sequence
from dataclasses import dataclass, field

from code_harness.domain.models.capability import StrategyOutcome, ToolWarning
from code_harness.domain.models.result_truncation import ResultTruncation


@dataclass(frozen=True, slots=True)
class ToolResult[T]:
    data: T
    elapsed_ms: int
    truncated: bool = False
    truncation: ResultTruncation | None = None
    warnings: tuple[ToolWarning, ...] = ()
    index_state: str | None = None
    strategies: tuple[StrategyOutcome, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.truncation is not None and self.truncation.truncated and not self.truncated:
            object.__setattr__(self, "truncated", True)


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
    warnings: Sequence[str | ToolWarning],
    *,
    code: str = "tool_warning",
    capability: str | None = None,
) -> tuple[ToolWarning, ...]:
    return tuple(
        dict.fromkeys(
            as_tool_warning(warning, code=code, capability=capability) for warning in warnings
        )
    )
