"""MCP elicitation adapter for the generic human-decision channel."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp import types
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from code_harness.domain.enums import ApprovalDecisionSource, HumanDecisionOutcome
from code_harness.domain.models.human_decision import HumanDecisionRequest, HumanDecisionResult

_LOGGER = logging.getLogger(__name__)


class ExecutionConfirmation(BaseModel):
    """Primitive-only form returned by an interactive MCP client."""

    decision: str = Field(description="Approve this exact one-time execution, or decline it.")


class McpElicitationDecisionChannel:
    """HumanDecisionChannel backed by MCP form elicitation."""

    def __init__(self, context: Context[Any, Any, Any]) -> None:
        self._context = context

    @property
    def source(self) -> str:
        return ApprovalDecisionSource.MCP_ELICITATION.value

    def supports_client(self) -> bool:
        required = types.ClientCapabilities(
            elicitation=types.ElicitationCapability(form=types.FormElicitationCapability())
        )
        return bool(self._context.session.check_client_capability(required))

    async def request_decision(
        self,
        request: HumanDecisionRequest,
        *,
        timeout_seconds: float,
    ) -> HumanDecisionResult:
        try:
            response = await asyncio.wait_for(
                self._context.elicit(request.summary, ExecutionConfirmation),
                timeout=timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError):
            return HumanDecisionResult(
                outcome=HumanDecisionOutcome.TIMED_OUT,
                source=self.source,
            )
        except Exception:
            _LOGGER.info("MCP execution elicitation became unavailable.", exc_info=True)
            return HumanDecisionResult(
                outcome=HumanDecisionOutcome.UNAVAILABLE,
                source=self.source,
            )

        action = response.action
        if action == "accept":
            decision = getattr(getattr(response, "data", None), "decision", None)
            if decision == "approve":
                return HumanDecisionResult(
                    outcome=HumanDecisionOutcome.APPROVED,
                    source=self.source,
                )
            return HumanDecisionResult(
                outcome=HumanDecisionOutcome.DENIED,
                source=self.source,
            )
        if action == "decline":
            return HumanDecisionResult(
                outcome=HumanDecisionOutcome.DENIED,
                source=self.source,
            )
        return HumanDecisionResult(
            outcome=HumanDecisionOutcome.CANCELLED,
            source=self.source,
        )

    def shutdown(self) -> None:
        return
