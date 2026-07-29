# Change session cleanup

## Retention defaults

| State | Retention |
| --- | --- |
| Applied / rejected / unchanged | Immediate cleanup |
| Conflict / failed integration | 7 days (`CODE_HARNESS_CHANGE_CONFLICT_RETENTION_HOURS=168`) |
| Abandoned ready/working/review | 24 hours |
| Crashed preparation | 1 hour |
| Audit metadata | 30 days |

## Safe deletion rules

Before removing a path the cleaner:

1. Resolves the path
2. Requires ancestry under `CODE_HARNESS_HOME/change-sessions`
3. Rejects filesystem roots, the primary workspace, and user home
4. Validates temporary branch names match `code-harness/session/...`
5. Runs idempotently (second run is a no-op)

## Commands

```powershell
code-harness changes cleanup --dry-run
code-harness changes cleanup --expired
code-harness changes recover <session-id>
```

Recovery on server start marks interrupted `applying` sessions as `failed` and
does not resume cherry-pick or mirror apply automatically.

## Blob GC

Blobs are removed when unreferenced, not needed for rollback, and outside
retention. GC runs after session cleanup and during retention sweeps.
