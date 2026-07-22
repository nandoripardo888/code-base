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


def test_mcp_handlers_match_application_tool_payloads(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)
    container = build_container(Settings.for_root(fixture_repository))

    list_request = ListFilesRequest()
    read_request = ReadFileRequest("src/agenda.py")
    search_request = SearchTextRequest("AgendaService")

    listed = _payload(asyncio.run(server.call_tool("list_files", {})))
    read = _payload(asyncio.run(server.call_tool("read_file", {"path": "src/agenda.py"})))
    searched = _payload(asyncio.run(server.call_tool("search_text", {"query": "AgendaService"})))

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


def test_mcp_handlers_map_typed_errors(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    payload = _payload(asyncio.run(server.call_tool("read_file", {"path": "../outside.py"})))

    assert payload["error"]["code"] == "path_outside_project"
    assert "data" not in payload


def test_mcp_handlers_map_invalid_query_errors(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)

    payload = _payload(asyncio.run(server.call_tool("search_files", {"query": "   "})))

    assert payload["error"]["code"] == "invalid_query"


def test_mcp_server_exposes_implemented_tool_parameters(fixture_repository: Path) -> None:
    server = create_server(fixture_repository)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

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


def test_settings_reads_mcp_expose_index_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_EXPOSE_INDEX", "true")

    settings = Settings.for_root(tmp_path)

    assert settings.mcp_expose_index_commands
