# ADR 0006: Change session isolation

Status: accepted

## Context

Agents need to modify code without writing directly into the user's primary
workspace. Workspaces may contain a single Git repository, no Git metadata, or
multiple repositories plus loose files. Integration must remain reviewable,
reversible on rejection, and recoverable after crashes.

## Decision

- The open project is always a `Workspace`. Git is optional.
- When isolation is enabled (`CODE_HARNESS_CHANGE_ISOLATION=auto|isolated`), the
  agent works only inside an isolated area under `CODE_HARNESS_HOME`:
  - single or nested Git repositories use detached temporary worktrees created
    from synthetic commits that snapshot the current tracked, staged, unstaged,
    and untracked workspace state without changing its index or `HEAD`;
  - non-Git areas use physical mirrors (no symlinks/junctions/hard links).
- Snapshots and content-addressed blobs record base and proposed versions; they
  are not the agent working directory.
- Agent-authored Git edits use the Codex patch envelope and create
  content-addressed checkpoints. Undo, redo, and restore are atomic and validate
  the expected active checkpoint and file hashes.
- Git preparation creates a candidate commit through a temporary index without
  moving the detached worktree's `HEAD`.
- Git acceptance applies the reviewed file manifest to the current workspace.
  It preserves identical/concurrent non-overlapping changes through a
  three-way merge and aborts all writes on stale or conflicting files. It does
  not move the user's branch, index, or `HEAD`.
- Non-Git acceptance applies proposed blobs with per-file replace and a journal;
  stale hashes abort before any mutation.
- Composite workspaces create one parent session with per-segment resources.
  Multi-repo integration is not atomic; results are journaled per segment.
- `accept` is bound to an exact `candidate_digest`. MCP does not expose generic
  destructive cleanup tools; retention/GC is server-managed.
- `in_place` remains disabled for MCP.

## Consequences

- The primary workspace stays unchanged until explicit acceptance.
- Temporary worktrees, mirrors, and unreferenced blobs are cleaned
  according to retention policy.
- Interrupted applies are recovered to `failed` and never auto-continued.
- A new patch after undo supersedes the abandoned redo branch, matching the
  linear checkpoint behavior expected by editor-style restore workflows.
