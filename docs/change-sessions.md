# Change sessions

Isolated change sessions let an agent edit code without touching the primary
workspace until you accept the candidate digest.

## Modes

| Topology | Isolation |
| --- | --- |
| Single Git repo | Detached temporary worktree based on a workspace snapshot |
| Non-Git folder | Physical mirror copy |
| Multiple repos (+ loose files) | Composite session (per-segment worktrees/mirrors) |

Configure with `CODE_HARNESS_CHANGE_ISOLATION` (`auto`, `isolated`, `in_place`).
Default is `auto`. `in_place` is not exposed over MCP.

`create_change_session` also accepts an optional `paths` array. Paths are
workspace-relative, normalized, deduplicated, and mapped to the most specific
topology segment. They select which worktrees or mirrors are created; they do
not restrict later edits within a selected segment. Omitting `paths` preserves
full-workspace discovery.

Git workspaces no longer need to be clean. Session creation builds a synthetic
snapshot commit with a temporary Git index, so tracked, staged, unstaged, and
untracked files are copied into the detached worktree without modifying the
user's index, branch, or `HEAD`. Ignored files selected by `.worktreeinclude`
are copied after the worktree is created.

## Lifecycle

1. `create` / `code-harness changes start` — snapshot the current workspace and
   create a detached isolation
2. Agent edits through `apply_change_patch`; each successful patch creates a
   content-addressed checkpoint
3. `undo_change_patch`, `redo_change_patch`, or
   `restore_change_checkpoint` can restore agent-authored file states
4. `prepare` — create a detached candidate commit or mirror manifest and its
   exact `candidate_digest`
5. Review digest/diff
6. `accept --digest <exact>` or `reject`
7. Cleanup removes worktrees/mirrors; conflict sessions are retained

`get_change_diff` is side-effect free. Before preparation it returns
`state="draft"` by comparing the baseline with the current isolation, including
modified, removed, untracked, and binary files. After preparation it returns
the immutable `state="prepared"` diff stored for the candidate digest.
Composite results use `segment_id="composite"` and expose unambiguous
per-segment entries in `segments`.

Applying a new patch after an undo supersedes the abandoned redo branch.
Checkpoint operations accept `expected_checkpoint_id` for optimistic
concurrency and fail safely if the isolated files were edited outside the
recorded history.

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
`list_change_sessions`, `get_change_diff`, `prepare_change_session`,
`list_change_checkpoints`.

Isolated edit tools: `apply_change_patch`, `undo_change_patch`,
`redo_change_patch`, `restore_change_checkpoint`.

Side-effect tools: `accept_change_session`, `reject_change_session`.

Accept requires the exact prepared digest. Approval/deny tooling is not exposed
on MCP; use the host decision channel when interactive approval is configured.

An integration conflict preserves the candidate and isolation, marks both the
session and failed segment as `conflict`, and returns `integration_failure`
with the segment, strategy, relative paths, conflict kinds, and base/current/
proposed SHA-256 values. File contents are not included. The available actions
are `retry_accept` and `reject`; retry uses the same candidate digest and
existing approval, without another prepare step.

For Git sessions, acceptance compares every proposed file with both the
session's base blob and the current workspace. Non-overlapping concurrent text
changes are merged with Git's three-way merge machinery. Any stale or
conflicting file aborts the entire write before the primary workspace changes.
The primary branch, index, and `HEAD` are never moved by acceptance.

## Storage

Under `CODE_HARNESS_HOME`:

- `change-sessions/` — session trees
- `change-sessions.db` — metadata and journal
- `blobs/` — content-addressed base, proposed, and checkpoint file bodies
- `locks/` — write locks

Set `CODE_HARNESS_CHANGE_REQUIRE_CLEAN_GIT=1` only for compatibility with the
legacy clean-worktree restriction. The default is `0`.
