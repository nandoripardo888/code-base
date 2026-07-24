from __future__ import annotations

import json
from hashlib import sha256

from code_harness.domain.enums import CommandKind, ExecutionCapability
from code_harness.domain.models.execution import ApprovalDigest


def script_hash(script: str) -> str:
    return sha256(script.encode("utf-8")).hexdigest()


def compute_approval_digest(
    *,
    project_id: str,
    kind: CommandKind,
    executable: str | None,
    args: tuple[str, ...],
    script: str | None,
    cwd: str,
    timeout_seconds: float,
    max_output_bytes: int,
    capabilities: tuple[ExecutionCapability, ...],
    backend: str,
    policy_version: str,
    policy_name: str,
) -> ApprovalDigest:
    payload = {
        "project_id": project_id,
        "command_kind": kind.value,
        "executable": executable,
        "args": list(args),
        "script_hash": script_hash(script) if script is not None else None,
        "cwd": cwd,
        "timeout_seconds": timeout_seconds,
        "max_output_bytes": max_output_bytes,
        "capabilities": [item.value for item in capabilities],
        "backend": backend,
        "policy_name": policy_name,
        "policy_version": policy_version,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return ApprovalDigest(value=sha256(canonical.encode("utf-8")).hexdigest())
