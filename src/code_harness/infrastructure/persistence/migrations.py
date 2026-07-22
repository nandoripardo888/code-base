import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.errors import IndexCorruptedError
from code_harness.infrastructure.persistence.connection import connect_database
from code_harness.infrastructure.persistence.schema import (
    MIGRATION_1,
    MIGRATION_2,
    MIGRATION_3,
    MIGRATION_4,
    MIGRATION_5,
)

MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "initial_phase_two_schema", MIGRATION_1),
    (2, "phase_three_structural_schema", MIGRATION_2),
    (3, "phase_four_semantic_schema", MIGRATION_3),
    (4, "phase_four_interrupted_run_recovery", MIGRATION_4),
    (5, "canonical_symbol_signatures", MIGRATION_5),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def apply_migrations(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with connect_database(path) as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise IndexCorruptedError(
                    f"Index schema version {current} is newer than supported {SCHEMA_VERSION}.",
                    path=str(path),
                )
            for version, name, statements in MIGRATIONS:
                if version <= current:
                    continue
                try:
                    connection.execute("BEGIN IMMEDIATE")
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
    except IndexCorruptedError:
        raise
    except sqlite3.DatabaseError as error:
        raise IndexCorruptedError(
            "Could not initialize or migrate the SQLite index.", path=str(path)
        ) from error
