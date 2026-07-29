from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.errors import ChangeSessionStoreUnavailableError
from code_harness.infrastructure.changes.persistence.migrations import (
    apply_change_session_migrations,
)
from code_harness.infrastructure.persistence.connection import connect_database


class ContentAddressedBlobStore:
    def __init__(self, db_path: Path | str, blob_root: Path | str) -> None:
        self._db_path = Path(db_path)
        self._blob_root = Path(blob_root)

    def initialize(self) -> None:
        apply_change_session_migrations(self._db_path)
        self._blob_root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, *, ref_owner: str, ref_kind: str) -> str:
        self.initialize()
        blob_id = hashlib.sha256(content).hexdigest()
        target = self._blob_path(blob_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(content)
            tmp.replace(target)
        now = datetime.now(UTC).isoformat()
        try:
            with connect_database(self._db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO blobs(blob_id, size_bytes, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(blob_id) DO NOTHING
                    """,
                    (blob_id, len(content), now),
                )
                connection.execute(
                    """
                    INSERT INTO blob_references(blob_id, ref_owner, ref_kind, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(blob_id, ref_owner, ref_kind) DO NOTHING
                    """,
                    (blob_id, ref_owner, ref_kind, now),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error
        return blob_id

    def get(self, blob_id: str) -> bytes:
        path = self._blob_path(blob_id)
        if not path.is_file():
            raise FileNotFoundError(blob_id)
        return path.read_bytes()

    def add_reference(self, blob_id: str, *, ref_owner: str, ref_kind: str) -> None:
        self.initialize()
        now = datetime.now(UTC).isoformat()
        try:
            with connect_database(self._db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO blob_references(blob_id, ref_owner, ref_kind, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(blob_id, ref_owner, ref_kind) DO NOTHING
                    """,
                    (blob_id, ref_owner, ref_kind, now),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def release_reference(self, blob_id: str, *, ref_owner: str, ref_kind: str) -> None:
        self.initialize()
        try:
            with connect_database(self._db_path) as connection:
                connection.execute(
                    """
                    DELETE FROM blob_references
                    WHERE blob_id = ? AND ref_owner = ? AND ref_kind = ?
                    """,
                    (blob_id, ref_owner, ref_kind),
                )
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def release_owner(self, ref_owner: str) -> int:
        self.initialize()
        try:
            with connect_database(self._db_path) as connection:
                cursor = connection.execute(
                    "DELETE FROM blob_references WHERE ref_owner = ?",
                    (ref_owner,),
                )
                return int(cursor.rowcount or 0)
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error

    def gc(self) -> int:
        self.initialize()
        removed = 0
        try:
            with connect_database(self._db_path) as connection:
                orphans = connection.execute(
                    """
                    SELECT b.blob_id
                    FROM blobs b
                    LEFT JOIN blob_references r ON r.blob_id = b.blob_id
                    WHERE r.blob_id IS NULL
                    """
                ).fetchall()
                for row in orphans:
                    blob_id = row["blob_id"]
                    path = self._blob_path(blob_id)
                    if path.exists():
                        path.unlink(missing_ok=True)
                        removed += 1
                    connection.execute("DELETE FROM blobs WHERE blob_id = ?", (blob_id,))
        except (OSError, sqlite3.DatabaseError) as error:
            raise ChangeSessionStoreUnavailableError() from error
        return removed

    def _blob_path(self, blob_id: str) -> Path:
        return self._blob_root / blob_id[:2] / blob_id
