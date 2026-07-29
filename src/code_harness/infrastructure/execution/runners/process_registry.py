from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace

from code_harness.domain.errors import (
    ExecutionConcurrencyLimitError,
    ExecutionNotFoundError,
)
from code_harness.domain.models.execution import ExecutionResult
from code_harness.domain.protocols.execution_runtime import ExecutionTaskControl
from code_harness.infrastructure.execution.runners.concurrency import (
    ProjectExecutionLease,
    ProjectExecutionLimiter,
)


class _TaskControl:
    def __init__(self, publish: Callable[[ExecutionResult], None]) -> None:
        self._publish = publish
        self._lock = threading.Lock()
        self._cancelled = False
        self._reason: str | None = None
        self._terminator: Callable[[], None] | None = None

    @property
    def cancellation_requested(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def cancellation_reason(self) -> str | None:
        with self._lock:
            return self._reason

    def cancel(self, reason: str | None) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._reason = reason
            terminator = self._terminator
        if terminator is not None:
            terminator()

    def register_terminator(self, callback: Callable[[], None]) -> bool:
        with self._lock:
            if not self._cancelled:
                self._terminator = callback
                return True
        callback()
        return False

    def clear_terminator(self) -> None:
        with self._lock:
            self._terminator = None

    def publish(self, result: ExecutionResult) -> None:
        self._publish(result)


@dataclass(slots=True)
class _RegistryEntry:
    result: ExecutionResult
    control: _TaskControl
    lease: ProjectExecutionLease
    done: threading.Event
    thread: threading.Thread | None = None
    error: Exception | None = None
    propagate_errors: bool = False


class ProcessRegistry:
    """Own in-process execution tasks and bounded, redacted terminal results."""

    def __init__(
        self,
        limiter: ProjectExecutionLimiter,
        *,
        completed_result_limit: int = 100,
    ) -> None:
        self._limiter = limiter
        self._completed_result_limit = completed_result_limit
        self._lock = threading.RLock()
        self._active: dict[str, _RegistryEntry] = {}
        self._completed: OrderedDict[str, _RegistryEntry] = OrderedDict()
        self._closed = False

    def reserve(self, execution_id: str, initial: ExecutionResult) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("The execution registry is shutting down.")
            if execution_id in self._active or execution_id in self._completed:
                raise RuntimeError("Execution ID is already registered.")
            lease = self._limiter.try_acquire()
            if lease is None:
                raise ExecutionConcurrencyLimitError(self._limiter.max_concurrent)
            control = _TaskControl(lambda result: self._publish(execution_id, result))
            self._active[execution_id] = _RegistryEntry(
                result=initial,
                control=control,
                lease=lease,
                done=threading.Event(),
            )
            return lease.slot_index

    def discard(self, execution_id: str) -> None:
        with self._lock:
            entry = self._active.pop(execution_id, None)
        if entry is not None:
            entry.lease.release()
            entry.done.set()

    def submit(
        self,
        execution_id: str,
        operation: Callable[[ExecutionTaskControl], ExecutionResult],
        on_error: Callable[[Exception], ExecutionResult],
        *,
        propagate_errors: bool,
    ) -> None:
        with self._lock:
            entry = self._active.get(execution_id)
            if entry is None:
                raise ExecutionNotFoundError(execution_id)
            entry.propagate_errors = propagate_errors
            thread = threading.Thread(
                target=self._run,
                args=(execution_id, operation, on_error),
                name=f"code-harness-execution-{execution_id[:8]}",
                daemon=True,
            )
            entry.thread = thread
        try:
            thread.start()
        except Exception:
            self.discard(execution_id)
            raise

    def wait(self, execution_id: str) -> ExecutionResult:
        entry = self._entry(execution_id)
        entry.done.wait()
        with self._lock:
            current = self._completed.get(execution_id)
            if current is None:
                raise ExecutionNotFoundError(execution_id)
            error = current.error
            current.error = None
            result = current.result
        if error is not None:
            raise error
        return result

    def get(self, execution_id: str, *, include_output: bool = True) -> ExecutionResult:
        entry = self._entry(execution_id)
        result = entry.result
        if include_output:
            return result
        return replace(result, stdout="", stderr="")

    def terminate(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
        grace_seconds: float = 5.0,
    ) -> ExecutionResult:
        with self._lock:
            completed = self._completed.get(execution_id)
            if completed is not None:
                return completed.result
            entry = self._active.get(execution_id)
            if entry is None:
                raise ExecutionNotFoundError(execution_id)
            control = entry.control
            done = entry.done
        control.cancel(reason)
        if done.wait(timeout=grace_seconds):
            return self.get(execution_id)
        current = self.get(execution_id)
        return replace(
            current,
            warnings=(
                *current.warnings,
                "Cancellation was requested; supervised cleanup is still in progress.",
            ),
        )

    def shutdown(self, *, grace_seconds: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = tuple(self._active.values())
        for entry in entries:
            entry.control.cancel("code-harness runtime shutdown")
        deadline = time.monotonic() + grace_seconds
        for entry in entries:
            entry.done.wait(timeout=max(0.0, deadline - time.monotonic()))

    def _run(
        self,
        execution_id: str,
        operation: Callable[[ExecutionTaskControl], ExecutionResult],
        on_error: Callable[[Exception], ExecutionResult],
    ) -> None:
        entry = self._entry(execution_id)
        try:
            result = operation(entry.control)
        except Exception as error:
            result = on_error(error)
            if entry.propagate_errors:
                entry.error = error
        self._complete(execution_id, result)

    def _publish(self, execution_id: str, result: ExecutionResult) -> None:
        with self._lock:
            entry = self._active.get(execution_id)
            if entry is not None:
                entry.result = result

    def _complete(self, execution_id: str, result: ExecutionResult) -> None:
        with self._lock:
            entry = self._active.pop(execution_id, None)
            if entry is None:
                return
            entry.result = result
            self._completed[execution_id] = entry
            self._completed.move_to_end(execution_id)
            while len(self._completed) > self._completed_result_limit:
                self._completed.popitem(last=False)
        try:
            entry.lease.release()
        finally:
            entry.done.set()

    def _entry(self, execution_id: str) -> _RegistryEntry:
        with self._lock:
            entry = self._active.get(execution_id) or self._completed.get(execution_id)
            if entry is None:
                raise ExecutionNotFoundError(execution_id)
            return entry
