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
  - single or nested Git repositories use temporary worktrees and branches
    named `code-harness/session/<session-id>/<segment-id>`;
  - non-Git areas use physical mirrors (no symlinks/junctions/hard links).
- Snapshots and content-addressed blobs record base and proposed versions; they
  are not the agent working directory.
- Git acceptance integrates by `git cherry-pick` of the candidate commit after
  preflight. Conflicts abort and mark the session `conflict`.
- Non-Git acceptance applies proposed blobs with per-file replace and a journal;
  stale hashes abort before any mutation.
- Composite workspaces create one parent session with per-segment resources.
  Multi-repo integration is not atomic; results are journaled per segment.
- `accept` is bound to an exact `candidate_digest`. MCP does not expose generic
  destructive cleanup tools; retention/GC is server-managed.
- `in_place` remains disabled for MCP.

## Consequences

- The primary workspace stays unchanged until explicit acceptance.
- Temporary worktrees, branches, mirrors, and unreferenced blobs are cleaned
  according to retention policy.
- Interrupted applies are recovered to `failed` and never auto-continued.
