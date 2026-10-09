from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

import code_harness.mcp.server as server_module
from code_harness.mcp.image_card import MAX_IMAGE_BYTES, UI_URI
from code_harness.mcp.server import create_server
from code_harness.mcp.tool_policy import resolve_tool_policy
from code_harness.projects import ProjectRegistry
from code_harness.session import Session

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4i8AAAAASUVORK5CYII="
)


def test_card_protocol_and_private_image_payload(session: Session, project: Path) -> None:
    (project / "preview.png").write_bytes(PNG)
    server = create_server(session=session)
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "ShowImage")
    assert tool.meta and tool.meta["ui"]["resourceUri"] == UI_URI
    assert tool.meta["openai/outputTemplate"] == UI_URI
    assert tool.annotations and tool.annotations.read_only_hint
    resource = next(iter(asyncio.run(server.read_resource(UI_URI))))
    assert resource.mime_type == "text/html;profile=mcp-app"
    assert resource.meta and resource.meta["ui"]["prefersBorder"] is False
    assert "ui/initialize" in str(resource.content)

    result = asyncio.run(server.call_tool("ShowImage", {"path": "preview.png", "title": "Teste"}))
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.meta
    images = result.meta["imageCard"]["images"]
    assert len(images) == 1 and images[0]["title"] == "Teste"
    encoded = images[0]["dataUrl"].split(",", 1)[1]
    assert base64.b64decode(encoded) == PNG
    assert encoded not in str(result.content)
    assert "Client rendering is unverified" in str(result.content)


def test_carousel_preserves_order_and_bytes(session: Session, project: Path) -> None:
    (project / "one.png").write_bytes(PNG)
    (project / "two.png").write_bytes(PNG + b"second")
    result = asyncio.run(
        create_server(session=session).call_tool(
            "ShowImage", {"path": ["two.png", "one.png"], "title": "Collection"}
        )
    )
    assert isinstance(result, CallToolResult) and not result.is_error and result.meta
    card = result.meta["imageCard"]
    assert card["title"] == "Collection"
    assert [i["title"] for i in card["images"]] == ["two", "one"]
    assert base64.b64decode(card["images"][0]["dataUrl"].split(",", 1)[1]) == PNG + b"second"


@pytest.mark.parametrize("paths", [[], ["one.png"] * 9, ["one.png", "../outside.png"]])
def test_carousel_rejects_invalid_batch_without_partial_payload(
    session: Session, project: Path, paths: list[str]
) -> None:
    (project / "one.png").write_bytes(PNG)
    result = asyncio.run(create_server(session=session).call_tool("ShowImage", {"path": paths}))
    assert isinstance(result, CallToolResult) and result.is_error and not result.meta


def test_carousel_total_size_limit(session: Session, project: Path) -> None:
    (project / "large.png").write_bytes(b"x" * MAX_IMAGE_BYTES)
    result = asyncio.run(
        create_server(session=session).call_tool("ShowImage", {"path": ["large.png"] * 3})
    )
    assert isinstance(result, CallToolResult) and result.is_error and not result.meta


@pytest.mark.parametrize("titles", [["Home", "Equipe"], ["Home"], ["", "Equipe"]])
def test_carousel_captions(session: Session, project: Path, titles: list[str]) -> None:
    (project / "one.png").write_bytes(PNG)
    result = asyncio.run(
        create_server(session=session).call_tool(
            "ShowImage", {"path": ["one.png", "one.png"], "titles": titles}
        )
    )
    assert isinstance(result, CallToolResult)
    if titles == ["Home", "Equipe"]:
        assert result.meta
        assert [i["title"] for i in result.meta["imageCard"]["images"]] == titles
    else:
        assert result.is_error and not result.meta


@pytest.mark.parametrize("path", ["../outside.png", "README.md", "missing.png", "."])
def test_card_rejects_invalid_paths(session: Session, path: str) -> None:
    server = create_server(session=session)
    result = asyncio.run(server.call_tool("ShowImage", {"path": path}))
    assert isinstance(result, CallToolResult) and result.is_error
    assert not result.meta


def test_card_size_limit(session: Session, project: Path) -> None:
    (project / "large.png").write_bytes(b"x" * (MAX_IMAGE_BYTES + 1))
    result = asyncio.run(
        create_server(session=session).call_tool("ShowImage", {"path": "large.png"})
    )
    assert isinstance(result, CallToolResult) and result.is_error


def test_card_respects_allowlist_and_scope(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = create_server(session=session, tool_policy=resolve_tool_policy(["Read"]))
    assert asyncio.run(server.list_resources()) == []
    server = create_server(
        session=session, tool_policy=resolve_tool_policy(["ShowImage"], enforce_scopes=True)
    )
    monkeypatch.setattr(
        server_module,
        "get_access_token",
        lambda: AccessToken(token="test", client_id="test", scopes=["code.exec"]),
    )
    with pytest.raises(ToolError, match=r"code\.read"):
        asyncio.run(server.call_tool("ShowImage", {"path": "preview.png"}))


def test_card_selects_project_without_fallback(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "preview.png").write_bytes(PNG)
    registry = ProjectRegistry.create({"first": first, "second": second}, default_project="first")
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool("ShowImage", {"project": "second", "path": "preview.png"})
        )
        assert isinstance(result, CallToolResult) and not result.is_error
        result = asyncio.run(
            server.call_tool("ShowImage", {"project": "unknown", "path": "preview.png"})
        )
        assert isinstance(result, CallToolResult) and result.is_error
    finally:
        registry.shutdown()
