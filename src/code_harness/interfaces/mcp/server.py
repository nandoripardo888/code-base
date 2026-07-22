"""MCP server bootstrap for code-harness."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from code_harness.bootstrap.container import build_container
from code_harness.bootstrap.project_registry import resolve_active_project
from code_harness.bootstrap.settings import Settings
from code_harness.interfaces.mcp.handlers import register_handlers


def create_server(project: Path | str | None = None) -> FastMCP:
    """Create a FastMCP server bound to a single resolved project root."""
    root = resolve_active_project(Path(project) if project is not None else None)
    settings = Settings.for_root(root)
    container = build_container(settings)
    server = FastMCP(
        "code-harness",
        instructions=(
            "Local-first, traceable code retrieval for the active project. "
            "Tools return structured JSON envelopes with data, timings, and warnings."
        ),
    )
    register_handlers(server, container, settings)
    return server


def run_server(project: Path | str | None = None) -> None:
    """Run the MCP server over stdio for the resolved project root."""
    create_server(project).run(transport="stdio")
