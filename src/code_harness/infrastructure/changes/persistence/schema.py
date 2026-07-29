from __future__ import annotations

MIGRATION_1: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS change_sessions (
        session_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workspace_root TEXT NOT NULL,
        topology_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        candidate_digest TEXT,
        approval_id TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        git_details_json TEXT NOT NULL DEFAULT '[]',
        mirror_details_json TEXT NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_change_sessions_workspace_updated
    ON change_sessions(workspace_id, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_change_sessions_status
    ON change_sessions(status)
    """,
    """
    CREATE TABLE IF NOT EXISTS change_session_segments (
        session_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        relative_root TEXT NOT NULL,
        source_root TEXT NOT NULL,
        isolation_root TEXT NOT NULL,
        status TEXT NOT NULL,
        base_digest TEXT NOT NULL,
        candidate_digest TEXT,
        PRIMARY KEY(session_id, segment_id),
        FOREIGN KEY(session_id) REFERENCES change_sessions(session_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS change_session_files (
        session_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        path TEXT NOT NULL,
        operation TEXT NOT NULL,
        base_sha256 TEXT,
        proposed_sha256 TEXT,
        base_blob_id TEXT,
        proposed_blob_id TEXT,
        PRIMARY KEY(session_id, segment_id, path),
        FOREIGN KEY(session_id) REFERENCES change_sessions(session_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS change_session_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        segment_id TEXT,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(session_id) REFERENCES change_sessions(session_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_change_session_events_session_time
    ON change_session_events(session_id, occurred_at ASC)
    """,
    """
    CREATE TABLE IF NOT EXISTS change_session_approvals (
        approval_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        candidate_digest TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        FOREIGN KEY(session_id) REFERENCES change_sessions(session_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blob_references (
        blob_id TEXT NOT NULL,
        ref_owner TEXT NOT NULL,
        ref_kind TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(blob_id, ref_owner, ref_kind)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_blob_references_owner
    ON blob_references(ref_owner)
    """,
    """
    CREATE TABLE IF NOT EXISTS blobs (
        blob_id TEXT PRIMARY KEY,
        size_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cleanup_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        dry_run INTEGER NOT NULL DEFAULT 0,
        details_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
)
