from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.errors import ExecutionStoreUnavailableError
from code_harness.infrastructure.execution.persistence.schema import MIGRATION_1
from code_harness.infrastructure.persistence.connection import connect_database

MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "execution_approval_and_audit", MIGRATION_1),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def apply_execution_migrations(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with connect_database(path) as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise ExecutionStoreUnavailableError(
                    f"Execution schema version {current} is newer than supported {SCHEMA_VERSION}."
                )
            for version, name, statements in MIGRATIONS:
                if version <= current:
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, datetime.now(UTC).isoformat()),
                    )
                    connection.execute(f"PRAGMA user_version = {version}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            connection.execute("PRAGMA journal_mode = WAL")
            return SCHEMA_VERSION
    except ExecutionStoreUnavailableError:
        raise
    except (OSError, sqlite3.DatabaseError) as error:
        raise ExecutionStoreUnavailableError() from error
