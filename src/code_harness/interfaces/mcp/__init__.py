"""Optional MCP interface package.

Importing this package requires the optional ``mcp`` extra:
``pip install code-harness[mcp]``.
"""

from code_harness.interfaces.mcp.server import create_server, run_server

__all__ = ["create_server", "run_server"]
