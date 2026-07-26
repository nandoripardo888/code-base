MIGRATION_1: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        approval_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        digest TEXT NOT NULL,
        state TEXT NOT NULL,
        command_kind TEXT NOT NULL,
        command_summary TEXT NOT NULL,
        required_capabilities_json TEXT NOT NULL,
        backend TEXT NOT NULL,
        policy_name TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        ruleset_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_at TEXT,
        consumed_at TEXT,
        decision_reason TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_active_digest
    ON approval_requests(project_id, digest)
    WHERE state IN ('pending', 'approved')
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_approval_project_created
    ON approval_requests(project_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS executions (
        execution_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        digest TEXT,
        approval_id TEXT,
        state TEXT NOT NULL,
        command_kind TEXT NOT NULL,
        command_summary TEXT NOT NULL,
        backend TEXT NOT NULL,
        backend_guarantees_json TEXT NOT NULL,
        policy_decision TEXT NOT NULL,
        policy_name TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        ruleset_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        elapsed_ms INTEGER,
        exit_code INTEGER,
        stdout_bytes INTEGER NOT NULL DEFAULT 0,
        stderr_bytes INTEGER NOT NULL DEFAULT 0,
        stdout_sha256 TEXT,
        stderr_sha256 TEXT,
        stdout_truncated INTEGER NOT NULL DEFAULT 0,
        stderr_truncated INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        error_message TEXT,
        FOREIGN KEY(approval_id) REFERENCES approval_requests(approval_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_executions_project_created
    ON executions(project_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_capabilities (
        execution_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY(execution_id, capability, source),
        FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        execution_id TEXT,
        approval_id TEXT,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
        FOREIGN KEY(approval_id) REFERENCES approval_requests(approval_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_execution_events_project_time
    ON execution_events(project_id, occurred_at DESC)
    """,
)
