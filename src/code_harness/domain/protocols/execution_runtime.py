from collections.abc import Callable
from typing import Protocol

from code_harness.domain.models.execution import ExecutionResult


class ExecutionTaskControl(Protocol):
    @property
    def cancellation_requested(self) -> bool: ...

    @property
    def cancellation_reason(self) -> str | None: ...

    def register_terminator(self, callback: Callable[[], None]) -> bool:
        """Register process-tree termination, invoking it immediately if already cancelled."""
        ...

    def clear_terminator(self) -> None: ...

    def publish(self, result: ExecutionResult) -> None: ...


class ExecutionRegistry(Protocol):
    def reserve(self, execution_id: str, initial: ExecutionResult) -> int: ...

    def discard(self, execution_id: str) -> None: ...

    def submit(
        self,
        execution_id: str,
        operation: Callable[[ExecutionTaskControl], ExecutionResult],
        on_error: Callable[[Exception], ExecutionResult],
        *,
        propagate_errors: bool,
    ) -> None: ...

    def wait(self, execution_id: str) -> ExecutionResult: ...

    def get(self, execution_id: str, *, include_output: bool = True) -> ExecutionResult: ...

    def terminate(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
        grace_seconds: float = 5.0,
    ) -> ExecutionResult: ...

    def shutdown(self, *, grace_seconds: float = 10.0) -> None: ...
