# ADR 0005: Execution trust boundary

Status: accepted

## Context

Code Harness is a local-first, read-only retrieval tool. Introducing optional
command execution for agents creates a new trust boundary: inspected and
approved commands may run on the host, while retrieval remains unchanged.

## Decision

- Execution is optional and disabled by default (`execution_enabled=false`).
- E0 delivers contracts, deterministic policy, and inspection only. No user
  process is started. PowerShell inspection invokes only the harness-owned AST
  parser with `pwsh -NoProfile -NonInteractive`; user scripts are input data,
  never loaded or executed. No runners exist.
- Composition uses `ExecutionContainer | None` on `ApplicationContainer`, built
  lazily from `bootstrap/execution.py` only when enabled.
- `host_supervised` declares its current guarantees in every inspection. In
  E0 it applies no execution guarantees because no runner exists; it is not a
  sandbox and must not be named or marketed as one.
- Approval state and future audit storage belong under
  `<CODE_HARNESS_HOME>/executions/<project_id>/`, never under
  `<project>/.code-harness/`.
- MCP must not expose approval tools. E0 does not register any execution MCP
  tools; `mcp_expose_execution` remains off and unused.
- Python API methods return `ToolResult[T]`.

## Consequences

- Retrieval CI and Linux installs stay free of Windows execution dependencies.
- Agents can inspect risk and digests before any later run capability exists.
- Strong isolation requires a separate backend (`windows_sandbox`) in a later
  phase, with explicit `BackendGuarantees`.
