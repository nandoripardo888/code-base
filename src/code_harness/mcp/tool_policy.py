"""Tool exposure and OAuth scope policy for the MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass

ALL_TOOLS = frozenset(
    {
        "ServerInfo",
        "ListProjects",
        "ProjectInfo",
        "ReloadProjects",
        "Shell",
        "GetJobStatus",
        "Grep",
        "Glob",
        "Read",
        "Write",
        "StrReplace",
        "ApplyPatch",
        "OpenPatchReview",
        "RollbackPatch",
        "Delete",
    }
)

PUBLIC_SAFE_TOOLS = frozenset({"ServerInfo"})

TOOL_SCOPES: dict[str, str | None] = {
    "ServerInfo": None,
    "ListProjects": "code.read",
    "ProjectInfo": "code.read",
    "ReloadProjects": "code.write",
    "Shell": "code.exec",
    "GetJobStatus": "code.exec",
    "Grep": "code.read",
    "Glob": "code.read",
    "Read": "code.read",
    "Write": "code.write",
    "StrReplace": "code.write",
    "ApplyPatch": "code.write",
    "OpenPatchReview": "code.write",
    "RollbackPatch": "code.write",
    "Delete": "code.write",
}


def _split_names(values: list[str]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        names.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(names)


def _env_allowlist() -> tuple[str, ...] | None:
    raw = os.environ.get("CODE_HARNESS_MCP_TOOL_ALLOWLIST")
    if raw is None or not raw.strip():
        return None
    return _split_names([raw])


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Maximum tool surface and optional per-tool OAuth scope enforcement."""

    allowed_tools: frozenset[str] | None = None
    enforce_scopes: bool = False

    def allows(self, name: str) -> bool:
        return self.allowed_tools is None or name in self.allowed_tools

    def required_scope(self, name: str) -> str | None:
        return TOOL_SCOPES[name]

    @property
    def is_public_safe(self) -> bool:
        return (
            self.allowed_tools is not None
            and bool(self.allowed_tools)
            and self.allowed_tools <= PUBLIC_SAFE_TOOLS
        )


def resolve_tool_policy(
    allowlist: list[str] | None = None,
    *,
    enforce_scopes: bool = False,
) -> ToolPolicy:
    """Resolve an explicit/ENV tool allowlist and validate every tool name."""
    raw_names = _split_names(allowlist) if allowlist is not None else _env_allowlist()
    if raw_names is None:
        return ToolPolicy(enforce_scopes=enforce_scopes)

    names = frozenset(raw_names)
    unknown = sorted(names - ALL_TOOLS)
    if unknown:
        available = ", ".join(sorted(ALL_TOOLS))
        raise ValueError(
            f"Unknown MCP tool(s): {', '.join(unknown)}. Available tools: {available}."
        )
    if not names:
        raise ValueError("MCP tool allowlist cannot be empty.")
    return ToolPolicy(allowed_tools=names, enforce_scopes=enforce_scopes)
