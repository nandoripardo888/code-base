# Change sessions

Isolated change sessions let an agent edit code without touching the primary
workspace until you accept the candidate digest.

## Modes

| Topology | Isolation |
| --- | --- |
| Single Git repo | Temporary worktree + branch |
| Non-Git folder | Physical mirror copy |
| Multiple repos (+ loose files) | Composite session (per-segment worktrees/mirrors) |

Configure with `CODE_HARNESS_CHANGE_ISOLATION` (`auto`, `isolated`, `in_place`).
Default is `auto`. `in_place` is not exposed over MCP.

## Lifecycle

1. `create` / `code-harness changes start` — resolve topology, lock, create isolation
2. Agent works only under resolved `cwd` / `isolation_root`
3. `prepare` — candidate commit or mirror manifest + `candidate_digest`
4. Review digest/diff
5. `accept --digest <exact>` or `reject`
6. Cleanup removes worktrees/branches/mirrors; conflict sessions are retained

## CLI

```powershell
code-harness changes start
code-harness changes status <session-id>
code-harness changes list
code-harness changes prepare <session-id>
code-harness changes accept <session-id> --digest <digest>
code-harness changes reject <session-id>
code-harness changes cleanup --dry-run
code-harness changes recover <session-id>
```

## MCP

Enable with `CODE_HARNESS_MCP_EXPOSE_CHANGE_SESSIONS=1`.

Read/prepare tools: `create_change_session`, `get_change_session`,
`list_change_sessions`, `get_change_diff`, `prepare_change_session`.

Side-effect tools: `accept_change_session`, `reject_change_session`.

Accept requires the exact prepared digest. Approval/deny tooling is not exposed
on MCP; use the host decision channel when interactive approval is configured.

## Storage

Under `CODE_HARNESS_HOME`:

- `change-sessions/` — session trees
- `change-sessions.db` — metadata and journal
- `blobs/` — content-addressed file bodies
- `locks/` — write locks
