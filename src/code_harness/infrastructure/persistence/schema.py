MIGRATION_1 = (
    """
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE projects (
        project_id TEXT PRIMARY KEY,
        root TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL,
        warning_files INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE index_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        mode TEXT NOT NULL,
        state TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        duration_ms INTEGER,
        discovered_files INTEGER NOT NULL DEFAULT 0,
        new_files INTEGER NOT NULL DEFAULT 0,
        changed_files INTEGER NOT NULL DEFAULT 0,
        removed_files INTEGER NOT NULL DEFAULT 0,
        unchanged_files INTEGER NOT NULL DEFAULT 0,
        indexed_files INTEGER NOT NULL DEFAULT 0,
        warning_files INTEGER NOT NULL DEFAULT 0,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        error TEXT
    )
    """,
    """
    CREATE TABLE files (
        file_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        modified_at_ns INTEGER NOT NULL,
        language TEXT,
        encoding TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        indexed_at TEXT NOT NULL,
        UNIQUE(project_id, path)
    )
    """,
    "CREATE INDEX idx_files_project_path ON files(project_id, path)",
    """
    CREATE VIRTUAL TABLE file_fts USING fts5(
        project_id UNINDEXED,
        path UNINDEXED,
        content,
        tokenize = 'unicode61'
    )
    """,
)

MIGRATION_2 = (
    "ALTER TABLE files ADD COLUMN parser_name TEXT",
    "ALTER TABLE files ADD COLUMN parser_version TEXT",
    "ALTER TABLE files ADD COLUMN parse_state TEXT",
    "ALTER TABLE files ADD COLUMN parse_error TEXT",
    "ALTER TABLE files ADD COLUMN chunking_version TEXT",
    """
    CREATE TABLE symbols (
        symbol_id TEXT PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        qualified_name TEXT,
        kind TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        start_column INTEGER,
        end_column INTEGER,
        signature TEXT,
        parent_symbol_id TEXT
    )
    """,
    "CREATE INDEX idx_symbols_project_name ON symbols(project_id, name)",
    "CREATE INDEX idx_symbols_project_qualified ON symbols(project_id, qualified_name)",
    "CREATE INDEX idx_symbols_file ON symbols(file_id, start_line)",
    """
    CREATE TABLE code_references (
        reference_id TEXT PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        target_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        start_column INTEGER,
        end_column INTEGER,
        source_symbol_id TEXT
    )
    """,
    "CREATE INDEX idx_references_project_target ON code_references(project_id, target_name)",
    "CREATE INDEX idx_references_file ON code_references(file_id, start_line)",
    """
    CREATE TABLE chunks (
        chunk_id TEXT PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        symbol_id TEXT,
        parent_chunk_id TEXT
    )
    """,
    "CREATE INDEX idx_chunks_file_range ON chunks(file_id, start_line, end_line)",
    """
    CREATE TABLE parser_failures (
        failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        language TEXT,
        operation TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        parser_name TEXT,
        parser_version TEXT,
        error TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_parser_failures_project ON parser_failures(project_id, file_id)",
)

MIGRATION_3 = (
    "ALTER TABLE index_runs ADD COLUMN generated_embeddings INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE index_runs ADD COLUMN reused_embeddings INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE index_runs ADD COLUMN embedded_chunks INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE index_runs ADD COLUMN embedding_failures INTEGER NOT NULL DEFAULT 0",
    """
    CREATE TABLE embedding_cache (
        embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        provider_version TEXT NOT NULL,
        model_id TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        strategy TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        vector BLOB NOT NULL,
        generated_at TEXT NOT NULL,
        UNIQUE(provider, provider_version, model_id, dimensions, strategy, content_hash)
    )
    """,
    """
    CREATE INDEX idx_embedding_cache_identity_hash ON embedding_cache(
        provider, provider_version, model_id, dimensions, strategy, content_hash
    )
    """,
    """
    CREATE TABLE chunk_embeddings (
        chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
        embedding_id INTEGER NOT NULL REFERENCES embedding_cache(embedding_id) ON DELETE CASCADE,
        PRIMARY KEY(chunk_id, embedding_id)
    )
    """,
    "CREATE INDEX idx_chunk_embeddings_embedding ON chunk_embeddings(embedding_id, chunk_id)",
)

MIGRATION_4 = ("ALTER TABLE index_runs ADD COLUMN owner_pid INTEGER",)

MIGRATION_5 = (
    "ALTER TABLE symbols ADD COLUMN canonical_signature TEXT",
    "ALTER TABLE files ADD COLUMN signature_extractor_version TEXT",
    "UPDATE symbols SET canonical_signature = NULL WHERE canonical_signature IS NULL",
)
