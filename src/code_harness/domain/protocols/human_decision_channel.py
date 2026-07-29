from typing import Protocol

from code_harness.domain.models.human_decision import HumanDecisionRequest, HumanDecisionResult


class HumanDecisionChannel(Protocol):
    @property
    def source(self) -> str: ...

    async def request_decision(
        self,
        request: HumanDecisionRequest,
        *,
        timeout_seconds: float,
    ) -> HumanDecisionResult: ...

    def shutdown(self) -> None: ...
