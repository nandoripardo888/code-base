from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_with_mcp[T](
    project: Path,
    history: Path,
    action: Callable[[ClientSession], Awaitable[T]],
) -> T:
    async def run() -> T:
        python_path = str(REPOSITORY_ROOT / "src")
        inherited_python_path = os.environ.get("PYTHONPATH")
        if inherited_python_path:
            python_path += os.pathsep + inherited_python_path
        environment = {
            **os.environ,
            "PYTHONPATH": python_path,
            "CODE_HARNESS_HISTORY_DIR": str(history),
            "CODE_HARNESS_REVIEW_PORT": "0",
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "code_harness", "serve", "--project", str(project)],
            env=environment,
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(
                reader,
                writer,
                read_timeout_seconds=15,
            ) as session,
        ):
            await session.initialize()
            return await action(session)

    return asyncio.run(run())


def structured_result(result: Any) -> dict[str, Any]:
    value = result.structured_content
    if isinstance(value, dict):
        nested = value.get("result")
        if isinstance(nested, dict):
            return nested
        return value

    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    raise AssertionError(f"MCP result did not contain a JSON object: {result!r}")


def text_result(result: Any) -> str:
    return "\n".join(
        text for item in result.content if isinstance((text := getattr(item, "text", None)), str)
    )
