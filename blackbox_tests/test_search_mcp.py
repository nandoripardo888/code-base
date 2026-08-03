from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import run_with_mcp, text_result
from mcp import ClientSession


def test_search_contract_and_count_then_content_workflow(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "generated").mkdir()
    (project / "src" / "app.py").write_text("Target\nTarget\n", encoding="utf-8")
    (project / "generated" / "app.py").write_text("Target\n", encoding="utf-8")

    async def exercise(session: ClientSession) -> dict[str, Any]:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        count = text_result(
            await session.call_tool(
                "Grep",
                {
                    "pattern": "Target",
                    "output_mode": "count",
                    "exclude": "generated/**",
                },
            )
        )
        content = text_result(
            await session.call_tool(
                "Grep",
                {"pattern": "Target", "exclude": "generated/**", "head_limit": 1},
            )
        )
        return {"schema": tools["Grep"].inputSchema, "count": count, "content": content}

    result = run_with_mcp(project, tmp_path / "history", exercise)
    schema = result["schema"]
    assert "required" not in schema or "pattern" not in schema["required"]
    assert "references" in schema["properties"]["output_mode"]["enum"]
    assert "exclude" in schema["properties"]
    assert "reference_kind" in schema["properties"]
    assert result["count"].startswith("2 matches in 1 file\n\nsrc/app.py:2")
    assert "generated/app.py" not in result["count"]
    assert "src/app.py" in result["content"]
    assert "generated/app.py" not in result["content"]
