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
- E1–E2 add synchronous structured-process execution, Job Object supervision,
  exact single-use approval, and sanitized auditing.
- E3 adds a separate PowerShell execution gate. On `host_supervised`, every
  PowerShell script requires approval even when structured-process approval is
  relaxed. The approved digest includes the script hash and resolved `pwsh`
  executable.
- Approved scripts are written outside the workspace to a temporary directory
  with a protected current-user/System DACL and executed only as
  `pwsh -NoLogo -NoProfile -NonInteractive -File <script>`. The script is
  removed in `finally` and its body is not audited.
- E4 adds process-scoped asynchronous execution. `wait=false` is available to
  the Python API; polling and cancellation operate only through the same live
  `ExecutionContainer`. Closing the container cancels every active Job Object
  tree before the rest of the runtime shuts down.
- Per-project concurrency uses crash-safe, non-blocking file-lock slots outside
  the workspace. Capacity is reserved before approval consumption. Interrupted
  `starting`/`running` audit rows are recovered only when their slot is no
  longer owned by a live process.
- Completed output remains redacted and bounded in a 100-entry in-memory LRU.
  SQLite continues to persist only audit metadata, byte counts, hashes and
  redacted errors, never stdout/stderr bodies.
- Composition uses `ExecutionContainer | None` on `ApplicationContainer`, built
  lazily from `bootstrap/execution.py` only when enabled.
- `host_supervised` declares its current guarantees in every inspection. In
  E0 it applies no execution guarantees because no runner exists; it is not a
  sandbox and must not be named or marketed as one.
- Approval state and audit storage belong under
  `<CODE_HARNESS_HOME>/executions/<project_id>/`, never under
  `<project>/.code-harness/`.
- MCP must not expose approval tools. Through E3 it does not register execution
  tools; `mcp_expose_execution` remains off and unused.
- Python API methods return `ToolResult[T]`.

## Consequences

- Retrieval CI and Linux installs stay free of Windows execution dependencies.
- Agents can inspect risk and digests before any later run capability exists.
- PowerShell execution is non-interactive, approval-only, and available only on
  Windows with PowerShell 7. Synchronous execution remains the default.
- Asynchronous executions do not survive runtime or CLI process termination;
  cross-process status and cancellation require a future persistent supervisor.
- Strong isolation requires a separate backend (`windows_sandbox`) in a later
  phase, with explicit `BackendGuarantees`.
