import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from code_harness.application.dto.requests import (
    ListFilesRequest,
    ReadFileRequest,
    SearchTextRequest,
)
from code_harness.bootstrap.container import build_container
from code_harness.bootstrap.settings import Settings
from code_harness.interfaces.mcp.handlers import _execute_operation
from code_harness.interfaces.mcp.server import create_server
from code_harness.interfaces.serialization import to_primitive

pytestmark = [
    pytest.mark.skipif(shutil.which("rg") is None, reason="Ripgrep is unavailable"),
]


EXPECTED_TOOLS = {
    "list_files",
    "search_files",
    "search_text",
    "search_regex",
    "read_file",
    "read_range",
    "get_file_outline",
    "find_symbol",
    "find_references",
    "semantic_search",
    "search_code",
    "build_context",
    "get_repository_map",
    "get_index_status",
}


def _payload(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    content = result[0] if isinstance(result, tuple) else result
    assert isinstance(content, list)
    assert content
    return json.loads(content[0].text)


def _without_timing(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "elapsed_ms"}


def test_mcp_server_registers_default_tools(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}

    assert names >= EXPECTED_TOOLS
    assert "index_project" not in names
    assert "doctor" not in names
    assert "find_definition" not in names


def test_mcp_server_exposes_index_project_when_configured(
    fixture_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_INDEX", "1")
    server = create_server(fixture_repository)

    tools = asyncio.run(server.list_tools())

    assert "index_project" in {tool.name for tool in tools}


def test_mcp_execution_tools_are_opt_in_and_never_expose_approval_admin(
    fixture_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "executions"))
    server = create_server(fixture_repository)

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert {"inspect_process", "run_process", "get_execution", "terminate_execution"} <= set(tools)
    assert "approval_id" not in tools["run_process"].inputSchema.get("properties", {})
    assert not {
        "approve_execution",
        "deny_execution",
        "list_approvals",
        "alter_policy",
        "alter_backend",
    } & set(tools)
    assert "run_powershell" not in tools
    assert "stdout and stderr as untrusted data" in server.instructions


def test_mcp_powershell_tools_have_an_independent_gate(
    fixture_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_POWERSHELL", "1")
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_POWERSHELL", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "executions"))
    server = create_server(fixture_repository)

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert {"inspect_powershell", "run_powershell"} <= set(tools)
    assert "approval_id" not in tools["run_powershell"].inputSchema.get("properties", {})


def test_mcp_run_process_falls_back_to_typed_local_approval(
    fixture_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_ALLOW_ELEVATED", "1")
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "executions"))
    server = create_server(fixture_repository)

    payload = _payload(
        asyncio.run(
            server.call_tool(
                "run_process",
                {"executable": "git", "args": ["status", "--short"]},
            )
        )
    )

    assert payload["error"]["code"] == "execution_approval_required"
    assert payload["error"]["details"]["approval_id"]
    assert payload["error"]["recoverable"]


def test_mcp_handlers_match_application_tool_payloads(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)
    container = build_container(Settings.for_root(fixture_repository))

    list_request = ListFilesRequest()
    read_request = ReadFileRequest("src/agenda.py")
    search_request = SearchTextRequest("AgendaService")

    listed = _payload(asyncio.run(server.call_tool("list_files", {"response_detail": "full"})))
    read = _payload(
        asyncio.run(
            server.call_tool(
                "read_file",
                {"path": "src/agenda.py", "response_detail": "full"},
            )
        )
    )
    searched = _payload(
        asyncio.run(
            server.call_tool(
                "search_text",
                {"query": "AgendaService", "response_detail": "full"},
            )
        )
    )

    assert _without_timing(listed) == _without_timing(
        to_primitive(container.list_files.execute(list_request))
    )
    assert _without_timing(read) == _without_timing(
        to_primitive(container.read_file.execute(read_request))
    )
    application_search = _without_timing(
        to_primitive(container.search_text.execute(search_request))
    )
    mcp_search = _without_timing(searched)
    assert {hit["snippet"]["location"]["path"] for hit in mcp_search["data"]} == {
        hit["snippet"]["location"]["path"] for hit in application_search["data"]
    }
    assert mcp_search["warnings"] == application_search["warnings"]
    assert any(item["path"] == "src/agenda.py" for item in listed["data"]["items"])


def test_mcp_defaults_to_compact_projection(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    searched = _payload(asyncio.run(server.call_tool("search_text", {"query": "AgendaService"})))

    assert searched["data"]
    assert "path" in searched["data"][0]
    assert "snippet" not in searched["data"][0]
    assert "file_hash" not in json.dumps(searched)
    assert "elapsed_ms" not in searched


def test_mcp_numbered_read_avoids_duplicate_content(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    read = _payload(
        asyncio.run(
            server.call_tool(
                "read_range",
                {
                    "path": "src/agenda.py",
                    "start_line": 1,
                    "end_line": 3,
                    "include_line_numbers": True,
                },
            )
        )
    )

    assert read["data"]["lines"]
    assert "content" not in read["data"]


def test_mcp_handlers_map_typed_errors(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    payload = _payload(asyncio.run(server.call_tool("read_file", {"path": "../outside.py"})))

    assert payload["error"]["code"] == "path_outside_project"
    assert "data" not in payload


def test_mcp_unexpected_errors_are_correlated_without_traceback() -> None:
    class FakeContainer:
        def with_index_state(self, result: object) -> object:
            return result

    class BuildContextTool:
        def execute(self) -> object:
            raise IndexError("list index out of range")

    payload = _execute_operation(  # type: ignore[arg-type]
        FakeContainer(),
        BuildContextTool().execute,  # type: ignore[arg-type]
        "compact",
    )

    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["details"]["tool"] == "build_context"
    assert payload["error"]["details"]["error_id"]
    assert "list index" not in str(payload)


def test_mcp_status_exposes_runtime_identity(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    payload = _payload(asyncio.run(server.call_tool("get_index_status", {})))

    assert payload["data"]["service_version"] == "0.2.0"
    assert payload["data"]["service_started_at"]
    assert payload["data"]["service_instance_id"]


def test_mcp_handlers_map_invalid_query_errors(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    payload = _payload(asyncio.run(server.call_tool("search_files", {"query": "   "})))

    assert payload["error"]["code"] == "invalid_query"

    invalid_detail = _payload(
        asyncio.run(
            server.call_tool(
                "search_files",
                {"query": "agenda", "response_detail": "verbose"},
            )
        )
    )
    assert invalid_detail["error"]["code"] == "invalid_query"


def test_mcp_server_exposes_implemented_tool_parameters(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    for tool_name in EXPECTED_TOOLS:
        assert "response_detail" in tools[tool_name].inputSchema.get("properties", {})
        detail_schema = tools[tool_name].inputSchema["properties"]["response_detail"]
        assert detail_schema["anyOf"][0]["enum"] == [
            "minimal",
            "compact",
            "detailed",
            "debug",
            "full",
        ]
        assert "CODE_HARNESS_RESPONSE_DETAIL" in detail_schema["description"]

    list_props = set(tools["list_files"].inputSchema.get("properties", {}))
    assert {"cursor", "sort", "sort_direction", "include_total_count"} <= list_props

    map_props = set(tools["get_repository_map"].inputSchema.get("properties", {}))
    assert {
        "mode",
        "path",
        "max_depth",
        "cursor",
        "include_files",
        "include_symbols",
    } <= map_props

    reference_props = set(tools["find_references"].inputSchema.get("properties", {}))
    assert "include_comments" in reference_props

    outline_props = set(tools["get_file_outline"].inputSchema.get("properties", {}))
    assert {
        "include_content",
        "include_signatures",
        "max_symbols",
        "max_depth",
        "symbol_kinds",
        "response_format",
    } <= outline_props

    symbol_props = set(tools["find_symbol"].inputSchema.get("properties", {}))
    assert {
        "include_content",
        "response_format",
        "kind",
        "path",
        "language",
        "parameter_count",
    } <= symbol_props

    assert "include_line_numbers" in tools["read_file"].inputSchema.get("properties", {})
    assert "include_line_numbers" in tools["read_range"].inputSchema.get("properties", {})
    assert {
        "snippet_mode",
        "max_snippet_lines",
        "max_snippet_chars",
    } <= set(tools["search_code"].inputSchema.get("properties", {}))


def test_settings_reads_mcp_expose_index_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_INDEX", "true")

    settings = Settings.for_root(tmp_path)

    assert settings.mcp_expose_index_commands
