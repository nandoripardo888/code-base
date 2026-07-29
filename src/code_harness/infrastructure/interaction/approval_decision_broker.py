from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from code_harness.domain.models.human_decision import HumanDecisionResult


@dataclass(slots=True)
class DecisionWaiter:
    _loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    _result: HumanDecisionResult | None = None

    def publish(self, decision: HumanDecisionResult) -> None:
        """Record a decision produced by any thread and wake the awaiting coroutine."""
        if self._result is not None:
            return
        self._result = decision
        # A closed loop raises; the result stays readable for late callers.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._event.set)

    @property
    def result(self) -> HumanDecisionResult | None:
        return self._result

    async def wait(self, *, timeout_seconds: float) -> HumanDecisionResult | None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return None
        return self._result


class ApprovalDecisionBroker:
    """In-process broker that wakes waiters when a local decision arrives."""

    def __init__(self) -> None:
        self._waiters: dict[str, DecisionWaiter] = {}
        self._lock = asyncio.Lock()

    async def register(self, approval_id: str) -> DecisionWaiter:
        async with self._lock:
            existing = self._waiters.get(approval_id)
            if existing is not None:
                return existing
            waiter = DecisionWaiter()
            self._waiters[approval_id] = waiter
            return waiter

    def publish(self, approval_id: str, decision: HumanDecisionResult) -> bool:
        waiter = self._waiters.get(approval_id)
        if waiter is None:
            return False
        waiter.publish(decision)
        return True

    async def unregister(self, approval_id: str) -> None:
        async with self._lock:
            self._waiters.pop(approval_id, None)

    def shutdown(self) -> None:
        for waiter in list(self._waiters.values()):
            if waiter.result is None:
                from code_harness.domain.enums import (
                    ApprovalDecisionSource,
                    HumanDecisionOutcome,
                )

                waiter.publish(
                    HumanDecisionResult(
                        outcome=HumanDecisionOutcome.UNAVAILABLE,
                        source=ApprovalDecisionSource.HOST_LOOPBACK.value,
                        reason="host_loopback_shutdown",
                    )
                )
        self._waiters.clear()
