import asyncio
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from code_harness.domain.enums import (
    ApprovalDecisionSource,
    ExecutionRiskSeverity,
    HumanDecisionOutcome,
)
from code_harness.domain.models.execution import RiskFinding
from code_harness.domain.models.human_decision import (
    DecisionDetail,
    DecisionOption,
    HumanDecisionRequest,
)
from code_harness.infrastructure.interaction import HostLoopbackDecisionChannel


def _request(approval_id: str = "approval-1") -> HumanDecisionRequest:
    return HumanDecisionRequest(
        request_id=approval_id,
        subject_kind="process_execution",
        subject_digest="digest-1",
        title="Confirm a one-time supervised execution",
        summary="Confirm git status",
        details=(DecisionDetail("command", "Command", "git status"),),
        risks=(
            RiskFinding(
                code="workspace_read",
                severity=ExecutionRiskSeverity.LOW,
                message="Reads workspace files",
            ),
        ),
        options=(
            DecisionOption("approve", "Approve once", HumanDecisionOutcome.APPROVED),
            DecisionOption("decline", "Decline", HumanDecisionOutcome.DENIED),
        ),
        expires_at="2099-01-01T00:00:00+00:00",
    )


def _fetch_form(
    channel: HostLoopbackDecisionChannel,
    approval_id: str,
) -> tuple[str, dict[str, str]]:
    response = urllib.request.urlopen(
        urllib.request.Request(
            f"{channel.base_url}/approvals/{approval_id}",
            headers={"Host": f"127.0.0.1:{channel.bound_port}"},
        ),
        timeout=2,
    )
    form_html = response.read().decode("utf-8")
    token_marker = 'name="csrf_token" value="'
    start = form_html.index(token_marker) + len(token_marker)
    end = form_html.index('"', start)
    return form_html[start:end], dict(response.headers)


def _post_decision(
    channel: HostLoopbackDecisionChannel,
    approval_id: str,
    decision: str,
    *,
    origin: str | None = None,
    sec_fetch_site: str | None = None,
) -> None:
    token, _ = _fetch_form(channel, approval_id)
    body = urllib.parse.urlencode({"csrf_token": token, "decision": decision}).encode("utf-8")
    headers = {
        "Host": f"127.0.0.1:{channel.bound_port}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": channel.base_url if origin is None else origin,
    }
    if sec_fetch_site is not None:
        headers["Sec-Fetch-Site"] = sec_fetch_site
    urllib.request.urlopen(
        urllib.request.Request(
            f"{channel.base_url}/approvals/{approval_id}",
            data=body,
            method="POST",
            headers=headers,
        ),
        timeout=2,
    )


def test_host_loopback_approves_exact_request(tmp_path: Path) -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> object:
            waiter = asyncio.create_task(
                channel.request_decision(_request(), timeout_seconds=5)
            )
            await asyncio.sleep(0.05)
            await asyncio.to_thread(_post_decision, channel, "approval-1", "approve")
            return await waiter

        result = asyncio.run(exercise())
        assert result.outcome is HumanDecisionOutcome.APPROVED
        assert result.source == ApprovalDecisionSource.HOST_LOOPBACK.value
    finally:
        channel.shutdown()


def test_host_loopback_serves_referrer_policy_that_preserves_origin() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> dict[str, str]:
            task = asyncio.create_task(
                channel.request_decision(_request("approval-headers"), timeout_seconds=2)
            )
            await asyncio.sleep(0.05)
            _, headers = await asyncio.to_thread(_fetch_form, channel, "approval-headers")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return headers

        headers = asyncio.run(exercise())
        assert headers["Referrer-Policy"] == "same-origin"
    finally:
        channel.shutdown()


def test_host_loopback_accepts_opaque_origin_from_same_origin_form() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> object:
            waiter = asyncio.create_task(
                channel.request_decision(_request("approval-opaque"), timeout_seconds=5)
            )
            await asyncio.sleep(0.05)
            await asyncio.to_thread(
                _post_decision,
                channel,
                "approval-opaque",
                "approve",
                origin="null",
                sec_fetch_site="same-origin",
            )
            return await waiter

        result = asyncio.run(exercise())
        assert result.outcome is HumanDecisionOutcome.APPROVED
    finally:
        channel.shutdown()


def test_host_loopback_rejects_opaque_origin_from_cross_site_form() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> object:
            waiter = asyncio.create_task(
                channel.request_decision(_request("approval-cross"), timeout_seconds=0.5)
            )
            await asyncio.sleep(0.05)

            def cross_site_post() -> None:
                with pytest.raises(urllib.error.HTTPError) as excinfo:
                    _post_decision(
                        channel,
                        "approval-cross",
                        "approve",
                        origin="null",
                        sec_fetch_site="cross-site",
                    )
                assert excinfo.value.code == 403

            await asyncio.to_thread(cross_site_post)
            return await waiter

        result = asyncio.run(exercise())
        assert result.outcome is HumanDecisionOutcome.TIMED_OUT
    finally:
        channel.shutdown()


def test_host_loopback_wakes_waiter_without_waiting_for_timeout() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> float:
            waiter = asyncio.create_task(
                channel.request_decision(_request("approval-wake"), timeout_seconds=30)
            )
            await asyncio.sleep(0.05)
            started = time.monotonic()
            await asyncio.to_thread(_post_decision, channel, "approval-wake", "approve")
            result = await waiter
            assert result.outcome is HumanDecisionOutcome.APPROVED
            return time.monotonic() - started

        elapsed = asyncio.run(exercise())
        assert elapsed < 2
    finally:
        channel.shutdown()


def test_host_loopback_rejects_bad_csrf_and_times_out() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> object:
            waiter = asyncio.create_task(
                channel.request_decision(_request("approval-2"), timeout_seconds=0.2)
            )
            await asyncio.sleep(0.05)

            def bad_post() -> None:
                body = urllib.parse.urlencode(
                    {"csrf_token": "forged", "decision": "approve"}
                ).encode("utf-8")
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"{channel.base_url}/approvals/approval-2",
                            data=body,
                            method="POST",
                            headers={
                                "Host": f"127.0.0.1:{channel.bound_port}",
                                "Content-Type": "application/x-www-form-urlencoded",
                            },
                        ),
                        timeout=2,
                    )
                except urllib.error.HTTPError as error:
                    assert error.code == 403

            await asyncio.to_thread(bad_post)
            return await waiter

        result = asyncio.run(exercise())
        assert result.outcome is HumanDecisionOutcome.TIMED_OUT
    finally:
        channel.shutdown()


def test_host_loopback_shutdown_is_idempotent() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    channel.shutdown()
    channel.shutdown()


def test_host_loopback_pending_queue_lists_open_requests() -> None:
    channel = HostLoopbackDecisionChannel(open_browser=False)
    try:
        async def exercise() -> str:
            task = asyncio.create_task(
                channel.request_decision(_request("approval-3"), timeout_seconds=2)
            )
            await asyncio.sleep(0.05)
            payload = await asyncio.to_thread(
                lambda: urllib.request.urlopen(
                    urllib.request.Request(
                        f"{channel.base_url}/pending",
                        headers={"Host": f"127.0.0.1:{channel.bound_port}"},
                    ),
                    timeout=2,
                ).read().decode("utf-8")
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return payload

        html = asyncio.run(exercise())
        assert "approval-3" in html
        assert "process_execution" in html
    finally:
        channel.shutdown()
