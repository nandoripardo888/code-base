# Architecture

The package is deliberately flat. A tool is a function that takes a `PathGuard`
(and, for shell tools, a `JobRegistry`) plus keyword arguments.

```mermaid
flowchart TB
    CLI[cli.py] --> Session
    MCP[mcp/server.py] --> Session
    Session[session.py<br/>PathGuard + JobRegistry] --> Tools
    subgraph Tools [tools/]
        Shell
        GetJobStatus
        Grep
        Glob
        Read
        Write
        StrReplace
        Delete
    end
    Shell --> Env[shell/environment.py]
    Shell --> Jobs[shell/background.py]
    GetJobStatus --> Jobs
    Grep --> RG[ripgrep.py]
    Glob --> RG
```

| Module | Responsibility |
|--------|----------------|
| `paths.py` | Resolves paths and rejects anything outside the allowed roots |
| `ripgrep.py` | Finds the `rg` executable and runs it |
| `errors.py` | The handful of typed failures, each with a stable `code` |
| `session.py` | Binds a project root to its path guard and shell jobs |
| `tools/` | One module per tool, no shared base class |
| `shell/environment.py` | Shell discovery, argv construction, syntax diagnostics |
| `shell/background.py` | Job registry, process lifecycle, bounded log tails |
| `mcp/server.py` | FastMCP registration over stdio |
| `cli.py` | The same tools behind typer commands |

There is no index, no database, no parser, and no ranking. `Grep` and `Glob`
shell out to ripgrep on every call, which is fast enough that caching would cost
more in staleness than it saves in time.

## Why paths are confined

`Write`, `StrReplace`, and `Delete` mutate the filesystem, so `PathGuard`
resolves each path and requires the result to sit under an allowed root. Shell
job logs live in a scratch directory outside the project and are never exposed
as readable paths; agents follow them only through `GetJobStatus`.

## Shell lifecycle

```mermaid
sequenceDiagram
    participant Agent
    participant Shell
    participant Registry as JobRegistry
    participant Process
    participant Status as GetJobStatus

    Agent->>Shell: command, block_until_ms, shell
    Shell->>Registry: launch + register
    Registry->>Process: spawn, stdout+stderr to private file
    Shell->>Process: wait(block_until_ms)
    alt finished in time
        Shell-->>Agent: status, exit_code, output, environment
    else still running
        Shell-->>Agent: status=running, job_id, last_output
        Agent->>Status: job_id, wait_ms, tail_lines
        Status-->>Agent: running | completed | failed
    end
```

Exit codes always come from `process.returncode`, never from log text. Job
status is derived from the process and exit code. `JobRegistry.cleanup()`
terminates whatever is still running and removes the scratch directory; the MCP
lifespan calls it when the server stops.

## Testing

`tests/` covers each tool against a temporary sample project. Tests that need
ripgrep are skipped when it is absent, so the suite still runs on a bare
machine.
