import json
import math
import os
import sqlite3
import struct
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from code_harness.domain.enums import IndexMode, IndexState, ParseState
from code_harness.domain.errors import IndexCorruptedError
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import (
    FileIndexUpdate,
    FtsCandidate,
    IndexedSource,
    IndexReport,
    IndexRunSummary,
    IndexStatus,
    StoredFile,
)
from code_harness.domain.models.project import Project
from code_harness.domain.models.semantic import (
    EmbeddableChunk,
    EmbeddingBatch,
    EmbeddingIdentity,
    EmbeddingRecord,
    Vector,
    VectorSearchHit,
)
from code_harness.domain.models.structural import (
    CodeReference,
    CodeSymbol,
    StructuralSearchResult,
)
from code_harness.infrastructure.filesystem.ignore_rules import compile_globs
from code_harness.infrastructure.parsers.signature_extractor import SIGNATURE_EXTRACTOR_VERSION
from code_harness.infrastructure.persistence.connection import connect_database
from code_harness.infrastructure.persistence.migrations import apply_migrations


class SQLiteRepositoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self, project: Project) -> None:
        apply_migrations(self.path)
        now = datetime.now().astimezone().isoformat()
        try:
            with connect_database(self.path) as connection:
                connection.execute(
                    """
                    INSERT INTO projects(project_id, root, state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        root = excluded.root,
                        updated_at = excluded.updated_at
                    """,
                    (project.project_id, project.root, IndexState.NOT_INITIALIZED.value, now, now),
                )
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not register the project in the index.") from error

    def list_files(self, project_id: str) -> tuple[StoredFile, ...]:
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT path, size_bytes, modified_at_ns, language, encoding,
                           content_hash, indexed_at, parser_name, parser_version,
                           parse_state, chunking_version
                    FROM files WHERE project_id = ? ORDER BY path
                    """,
                    (project_id,),
                ).fetchall()
            return tuple(
                StoredFile(
                    path=row["path"],
                    size_bytes=row["size_bytes"],
                    modified_at_ns=row["modified_at_ns"],
                    language=row["language"],
                    encoding=row["encoding"],
                    content_hash=row["content_hash"],
                    indexed_at=row["indexed_at"],
                    parser_name=row["parser_name"],
                    parser_version=row["parser_version"],
                    parse_state=row["parse_state"],
                    chunking_version=row["chunking_version"],
                )
                for row in rows
            )
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not read indexed file metadata.") from error

    def start_run(self, project_id: str, mode: IndexMode, started_at: str) -> int:
        self._recover_interrupted_runs(project_id)
        try:
            with connect_database(self.path) as connection:
                connection.execute(
                    "UPDATE projects SET state = ?, updated_at = ? WHERE project_id = ?",
                    (IndexState.INDEXING.value, started_at, project_id),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO index_runs(project_id, mode, state, started_at, owner_pid)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, mode.value, IndexState.INDEXING.value, started_at, os.getpid()),
                )
                if cursor.lastrowid is None:
                    raise self._corrupted("SQLite did not return an index run identifier.")
                return cursor.lastrowid
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not start the index run.") from error

    def commit_files(
        self,
        report: IndexReport,
        updates: tuple[FileIndexUpdate, ...],
        removed_paths: tuple[str, ...],
    ) -> None:
        try:
            with connect_database(self.path) as connection:
                for path in removed_paths:
                    connection.execute(
                        "DELETE FROM file_fts WHERE project_id = ? AND path = ?",
                        (report.project_id, path),
                    )
                    connection.execute(
                        "DELETE FROM files WHERE project_id = ? AND path = ?",
                        (report.project_id, path),
                    )
                for update in updates:
                    self._apply_update(connection, report, update)
        except sqlite3.DatabaseError as error:
            raise self._corrupted(f"Could not persist indexed files: {error}") from error

    def commit_embeddings(self, embeddings: EmbeddingBatch) -> None:
        if embeddings.identity is None:
            return
        try:
            with connect_database(self.path) as connection:
                self._apply_embeddings(connection, embeddings)
        except sqlite3.DatabaseError as error:
            raise self._corrupted(f"Could not persist semantic embeddings: {error}") from error

    def complete_run(self, run_id: int, report: IndexReport) -> None:
        duration_ms = _duration_ms(report.started_at, report.finished_at)
        try:
            with connect_database(self.path) as connection:
                connection.execute(
                    """
                    UPDATE index_runs SET
                        state = ?, finished_at = ?, duration_ms = ?, discovered_files = ?,
                        new_files = ?, changed_files = ?, removed_files = ?,
                        unchanged_files = ?, indexed_files = ?, warning_files = ?,
                        warnings_json = ?, generated_embeddings = ?, reused_embeddings = ?,
                        embedded_chunks = ?, embedding_failures = ?
                    WHERE run_id = ?
                    """,
                    (
                        report.state.value,
                        report.finished_at,
                        duration_ms,
                        report.discovered_files,
                        report.new_files,
                        report.changed_files,
                        report.removed_files,
                        report.unchanged_files,
                        report.indexed_files,
                        report.warning_files,
                        json.dumps(report.warnings),
                        report.generated_embeddings,
                        report.reused_embeddings,
                        report.embedded_chunks,
                        report.embedding_failures,
                        run_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE projects SET state = ?, warning_files = ?, last_error = NULL,
                                        updated_at = ?
                    WHERE project_id = ?
                    """,
                    (
                        report.state.value,
                        report.warning_files,
                        report.finished_at,
                        report.project_id,
                    ),
                )
        except sqlite3.DatabaseError as error:
            raise self._corrupted(f"Could not finalize the index run: {error}") from error

    def _apply_embeddings(self, connection: sqlite3.Connection, batch: EmbeddingBatch) -> None:
        identity = batch.identity
        if identity is None:
            return
        for record in batch.records:
            if record.identity != identity:
                raise self._corrupted("Embedding batch contains mixed model identities.")
            connection.execute(
                """
                INSERT INTO embedding_cache(
                    provider, provider_version, model_id, dimensions, strategy,
                    content_hash, vector, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_version, model_id, dimensions, strategy,
                            content_hash) DO NOTHING
                """,
                (
                    identity.provider,
                    identity.provider_version,
                    identity.model_id,
                    identity.dimensions,
                    identity.strategy,
                    record.content_hash,
                    _pack_vector(record.vector, identity.dimensions),
                    record.generated_at,
                ),
            )
        for link in batch.links:
            row = connection.execute(
                """
                SELECT embedding_id FROM embedding_cache
                WHERE provider = ? AND provider_version = ? AND model_id = ?
                  AND dimensions = ? AND strategy = ? AND content_hash = ?
                """,
                (*_identity_values(identity), link.content_hash),
            ).fetchone()
            if row is None:
                raise self._corrupted("Could not resolve a cached embedding for a chunk.")
            connection.execute(
                """
                INSERT INTO chunk_embeddings(chunk_id, embedding_id) VALUES (?, ?)
                ON CONFLICT(chunk_id, embedding_id) DO NOTHING
                """,
                (link.chunk_id, row["embedding_id"]),
            )

    def _apply_update(
        self,
        connection: sqlite3.Connection,
        report: IndexReport,
        update: FileIndexUpdate,
    ) -> None:
        source = update.source
        connection.execute(
            """
            INSERT INTO files(
                project_id, path, size_bytes, modified_at_ns, language,
                encoding, content_hash, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                modified_at_ns = excluded.modified_at_ns,
                language = excluded.language,
                encoding = excluded.encoding,
                content_hash = excluded.content_hash,
                indexed_at = excluded.indexed_at
            """,
            (
                report.project_id,
                source.path,
                source.size_bytes,
                source.modified_at_ns,
                source.language,
                source.encoding,
                source.content_hash,
                report.finished_at,
            ),
        )
        file_row = connection.execute(
            "SELECT file_id FROM files WHERE project_id = ? AND path = ?",
            (report.project_id, source.path),
        ).fetchone()
        if file_row is None:
            raise self._corrupted("Could not resolve the indexed file identifier.")
        file_id = int(file_row["file_id"])
        if update.update_content:
            connection.execute(
                "DELETE FROM file_fts WHERE project_id = ? AND path = ?",
                (report.project_id, source.path),
            )
            connection.execute(
                "INSERT INTO file_fts(project_id, path, content) VALUES (?, ?, ?)",
                (report.project_id, source.path, source.content),
            )
            self._replace_structure(
                connection,
                report.project_id,
                file_id,
                source,
                update,
                report.finished_at,
            )

    def _replace_structure(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        file_id: int,
        source: IndexedSource,
        update: FileIndexUpdate,
        recorded_at: str,
    ) -> None:
        for table in ("parser_failures", "chunks", "code_references", "symbols"):
            connection.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
        analysis = update.analysis
        if analysis is None:
            connection.execute(
                """
                UPDATE files SET parser_name = NULL, parser_version = NULL,
                                 parse_state = ?, parse_error = NULL,
                                 chunking_version = ?
                WHERE file_id = ?
                """,
                (ParseState.NOT_APPLICABLE.value, update.chunking_version, file_id),
            )
            return
        parse_error = (
            analysis.warnings[0]
            if analysis.state is ParseState.FAILED and analysis.warnings
            else None
        )
        connection.execute(
            """
            UPDATE files SET parser_name = ?, parser_version = ?, parse_state = ?,
                             parse_error = ?, chunking_version = ?,
                             signature_extractor_version = ?
            WHERE file_id = ?
            """,
            (
                analysis.parser_name,
                analysis.parser_version,
                analysis.state.value,
                parse_error,
                update.chunking_version,
                SIGNATURE_EXTRACTOR_VERSION,
                file_id,
            ),
        )
        for symbol in analysis.symbols:
            location = symbol.location
            connection.execute(
                """
                INSERT INTO symbols(
                    symbol_id, file_id, project_id, name, qualified_name, kind,
                    start_line, end_line, start_column, end_column, signature,
                    parent_symbol_id, canonical_signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.symbol_id,
                    file_id,
                    project_id,
                    symbol.name,
                    symbol.qualified_name,
                    symbol.kind,
                    location.start_line,
                    location.end_line,
                    location.start_column,
                    location.end_column,
                    symbol.signature,
                    symbol.parent_symbol_id,
                    symbol.canonical_signature,
                ),
            )
        for reference in analysis.references:
            location = reference.location
            connection.execute(
                """
                INSERT INTO code_references(
                    reference_id, file_id, project_id, target_name, kind,
                    start_line, end_line, start_column, end_column, source_symbol_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference.reference_id,
                    file_id,
                    project_id,
                    reference.target_name,
                    reference.kind,
                    location.start_line,
                    location.end_line,
                    location.start_column,
                    location.end_column,
                    reference.source_symbol_id,
                ),
            )
        for chunk in analysis.chunks:
            connection.execute(
                """
                INSERT INTO chunks(
                    chunk_id, file_id, project_id, start_line, end_line, content,
                    content_hash, kind, symbol_id, parent_chunk_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    file_id,
                    project_id,
                    chunk.location.start_line,
                    chunk.location.end_line,
                    chunk.content,
                    chunk.content_hash,
                    chunk.kind,
                    chunk.symbol_id,
                    chunk.parent_chunk_id,
                ),
            )
        if analysis.state is ParseState.FAILED:
            error = parse_error or "Structural parser failed."
            connection.execute(
                """
                INSERT INTO parser_failures(
                    file_id, project_id, language, operation, content_hash,
                    parser_name, parser_version, error, recorded_at
                ) VALUES (?, ?, ?, 'analyze', ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    project_id,
                    source.language,
                    source.content_hash,
                    analysis.parser_name,
                    analysis.parser_version,
                    error,
                    recorded_at,
                ),
            )

    def fail_run(self, run_id: int, finished_at: str, message: str) -> None:
        if not self.path.is_file():
            return
        try:
            with connect_database(self.path) as connection:
                row = connection.execute(
                    "SELECT project_id, started_at FROM index_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    return
                connection.execute(
                    """
                    UPDATE index_runs SET state = ?, finished_at = ?, duration_ms = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (
                        IndexState.FAILED.value,
                        finished_at,
                        _duration_ms(row["started_at"], finished_at),
                        message,
                        run_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE projects SET state = ?, last_error = ?, updated_at = ?
                    WHERE project_id = ?
                    """,
                    (IndexState.FAILED.value, message, finished_at, row["project_id"]),
                )
        except sqlite3.DatabaseError:
            return

    def get_status(self, project: Project) -> IndexStatus:
        if not self.path.is_file():
            return IndexStatus(project.project_id, IndexState.NOT_INITIALIZED, 0, 0, 0, 0)
        self._recover_interrupted_runs(project.project_id)
        try:
            with connect_database(self.path) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                project_row = connection.execute(
                    "SELECT state, warning_files, last_error FROM projects WHERE project_id = ?",
                    (project.project_id,),
                ).fetchone()
                if project_row is None:
                    return IndexStatus(
                        project.project_id, IndexState.NOT_INITIALIZED, version, 0, 0, 0
                    )
                file_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM files WHERE project_id = ?", (project.project_id,)
                    ).fetchone()[0]
                )
                fts_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM file_fts WHERE project_id = ?", (project.project_id,)
                    ).fetchone()[0]
                )
                structural_schema_ready = version >= 2
                symbol_count = (
                    self._count(connection, "symbols", project.project_id)
                    if structural_schema_ready
                    else 0
                )
                reference_count = (
                    self._count(connection, "code_references", project.project_id)
                    if structural_schema_ready
                    else 0
                )
                chunk_count = (
                    self._count(connection, "chunks", project.project_id)
                    if structural_schema_ready
                    else 0
                )
                parser_failure_count = (
                    self._count(connection, "parser_failures", project.project_id)
                    if structural_schema_ready
                    else 0
                )
                semantic_schema_ready = version >= 3
                embedding_count = (
                    int(connection.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0])
                    if semantic_schema_ready
                    else 0
                )
                embedded_chunk_count = (
                    int(
                        connection.execute(
                            """
                            SELECT COUNT(DISTINCT ce.chunk_id)
                            FROM chunk_embeddings ce
                            JOIN chunks c ON c.chunk_id = ce.chunk_id
                            WHERE c.project_id = ?
                            """,
                            (project.project_id,),
                        ).fetchone()[0]
                    )
                    if semantic_schema_ready
                    else 0
                )
                semantic_columns = (
                    "generated_embeddings, reused_embeddings"
                    if semantic_schema_ready
                    else "0 AS generated_embeddings, 0 AS reused_embeddings"
                )
                last_row = connection.execute(
                    f"""
                    SELECT mode, state, started_at, finished_at, duration_ms,
                           discovered_files, indexed_files, unchanged_files, warning_files,
                           warnings_json, {semantic_columns}
                    FROM index_runs WHERE project_id = ? ORDER BY run_id DESC LIMIT 1
                    """,
                    (project.project_id,),
                ).fetchone()
                last_run = _run_summary(last_row) if last_row is not None else None
                warnings_list = [project_row["last_error"]] if project_row["last_error"] else []
                if last_row is not None:
                    stored_warnings = json.loads(last_row["warnings_json"])
                    if isinstance(stored_warnings, list):
                        warnings_list.extend(str(item) for item in stored_warnings)
                if parser_failure_count:
                    warnings_list.append(
                        f"{parser_failure_count} file(s) have structural parser failures."
                    )
                if not structural_schema_ready:
                    warnings_list.append("Run index to migrate the structural schema.")
                return IndexStatus(
                    project_id=project.project_id,
                    state=IndexState(project_row["state"]),
                    schema_version=version,
                    file_count=file_count,
                    fts_document_count=fts_count,
                    warning_files=project_row["warning_files"],
                    symbol_count=symbol_count,
                    reference_count=reference_count,
                    chunk_count=chunk_count,
                    parser_failure_count=parser_failure_count,
                    structural_schema_ready=structural_schema_ready,
                    semantic_schema_ready=semantic_schema_ready,
                    embedding_count=embedding_count,
                    embedded_chunk_count=embedded_chunk_count,
                    last_run=last_run,
                    warnings=tuple(warnings_list),
                )
        except (sqlite3.DatabaseError, KeyError, ValueError) as error:
            raise self._corrupted("Could not inspect the SQLite index.") from error

    def _recover_interrupted_runs(self, project_id: str) -> None:
        recovered_at = datetime.now().astimezone().isoformat()
        try:
            with connect_database(self.path) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version < 4:
                    return
                rows = connection.execute(
                    """
                    SELECT run_id, started_at, owner_pid FROM index_runs
                    WHERE project_id = ? AND state = ?
                    """,
                    (project_id, IndexState.INDEXING.value),
                ).fetchall()
                interrupted = [
                    row
                    for row in rows
                    if row["owner_pid"] is None or not _pid_is_alive(row["owner_pid"])
                ]
                if not interrupted:
                    return
                message = "Recovered an interrupted index run; rerun indexing to complete it."
                for row in interrupted:
                    connection.execute(
                        """
                        UPDATE index_runs SET state = ?, finished_at = ?, duration_ms = ?, error = ?
                        WHERE run_id = ?
                        """,
                        (
                            IndexState.FAILED.value,
                            recovered_at,
                            _duration_ms(row["started_at"], recovered_at),
                            message,
                            row["run_id"],
                        ),
                    )
                file_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM files WHERE project_id = ?", (project_id,)
                    ).fetchone()[0]
                )
                recovered_state = (
                    IndexState.READY_WITH_WARNINGS if file_count else IndexState.FAILED
                )
                connection.execute(
                    """
                    UPDATE projects SET state = ?, warning_files = 1, last_error = ?, updated_at = ?
                    WHERE project_id = ?
                    """,
                    (recovered_state.value, message, recovered_at, project_id),
                )
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not recover interrupted index runs.") from error

    def get_cached_embeddings(
        self,
        identity: EmbeddingIdentity,
        content_hashes: Sequence[str],
    ) -> tuple[EmbeddingRecord, ...]:
        if not self.path.is_file() or not content_hashes:
            return ()
        unique_hashes = tuple(dict.fromkeys(content_hashes))
        records: list[EmbeddingRecord] = []
        try:
            with connect_database(self.path) as connection:
                for offset in range(0, len(unique_hashes), 500):
                    batch = unique_hashes[offset : offset + 500]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""
                        SELECT content_hash, vector, generated_at FROM embedding_cache
                        WHERE provider = ? AND provider_version = ? AND model_id = ?
                          AND dimensions = ? AND strategy = ?
                          AND content_hash IN ({placeholders})
                        """,
                        (*_identity_values(identity), *batch),
                    ).fetchall()
                    records.extend(
                        EmbeddingRecord(
                            identity,
                            row["content_hash"],
                            _unpack_vector(row["vector"], identity.dimensions),
                            row["generated_at"],
                        )
                        for row in rows
                    )
        except (sqlite3.DatabaseError, ValueError) as error:
            raise self._corrupted("Could not read the embedding cache.") from error
        return tuple(records)

    def list_unembedded_chunks(
        self,
        project_id: str,
        identity: EmbeddingIdentity,
    ) -> tuple[EmbeddableChunk, ...]:
        if not self.path.is_file():
            return ()
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT c.chunk_id, c.start_line, c.end_line, c.content,
                           c.content_hash, f.path, f.language, f.content_hash AS file_hash
                    FROM chunks c JOIN files f ON f.file_id = c.file_id
                    WHERE c.project_id = ? AND NOT EXISTS (
                        SELECT 1 FROM chunk_embeddings ce
                        JOIN embedding_cache e ON e.embedding_id = ce.embedding_id
                        WHERE ce.chunk_id = c.chunk_id
                          AND e.provider = ? AND e.provider_version = ? AND e.model_id = ?
                          AND e.dimensions = ? AND e.strategy = ?
                    )
                    ORDER BY f.path, c.start_line
                    """,
                    (project_id, *_identity_values(identity)),
                ).fetchall()
            return tuple(_embeddable_chunk(row) for row in rows)
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not list chunks missing embeddings.") from error

    def search_vectors(
        self,
        project_id: str,
        identity: EmbeddingIdentity,
        query_vector: Vector,
        *,
        limit: int,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
    ) -> tuple[VectorSearchHit, ...]:
        if not self.path.is_file():
            return ()
        query = _validated_vector(query_vector, identity.dimensions)
        include_spec = compile_globs(include_globs)
        exclude_spec = compile_globs(exclude_globs)
        language_set = {item.casefold() for item in languages}
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT c.chunk_id, c.start_line, c.end_line, c.content,
                           c.content_hash, f.path, f.language, f.content_hash AS file_hash,
                           e.vector
                    FROM chunk_embeddings ce
                    JOIN embedding_cache e ON e.embedding_id = ce.embedding_id
                    JOIN chunks c ON c.chunk_id = ce.chunk_id
                    JOIN files f ON f.file_id = c.file_id
                    WHERE c.project_id = ? AND e.provider = ? AND e.provider_version = ?
                      AND e.model_id = ? AND e.dimensions = ? AND e.strategy = ?
                    """,
                    (project_id, *_identity_values(identity)),
                ).fetchall()
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not search the semantic vector index.") from error
        hits: list[VectorSearchHit] = []
        try:
            for row in rows:
                path = str(row["path"])
                if include_spec is not None and not include_spec.match_file(path):
                    continue
                if exclude_spec is not None and exclude_spec.match_file(path):
                    continue
                language = row["language"]
                if language_set and (
                    language is None or str(language).casefold() not in language_set
                ):
                    continue
                vector = _unpack_vector(row["vector"], identity.dimensions)
                hits.append(VectorSearchHit(_embeddable_chunk(row), _cosine(query, vector)))
        except ValueError as error:
            raise self._corrupted("Stored semantic vectors are invalid.") from error
        hits.sort(
            key=lambda item: (
                -item.score,
                item.chunk.location.path,
                item.chunk.location.start_line,
            )
        )
        return tuple(hits[:limit])

    def search_fts(
        self,
        project_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[FtsCandidate, ...]:
        if not self.path.is_file():
            return ()
        expression = '"' + query.replace('"', '""') + '"'
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT path, bm25(file_fts) AS rank
                    FROM file_fts
                    WHERE project_id = ? AND file_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (project_id, expression, limit),
                ).fetchall()
            return tuple(FtsCandidate(row["path"], float(row["rank"])) for row in rows)
        except sqlite3.DatabaseError as error:
            raise self._corrupted("SQLite FTS search failed.") from error

    def get_outline(self, project_id: str, path: str) -> tuple[StructuralSearchResult, ...]:
        return self._read_symbols(
            "WHERE s.project_id = ? AND f.path = ? ORDER BY s.start_line, s.end_line",
            (project_id, path),
        )

    def find_symbols(
        self,
        project_id: str,
        query: str,
        *,
        exact: bool,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]:
        if exact:
            clause = (
                "WHERE s.project_id = ? AND "
                "(s.name = ? COLLATE NOCASE OR s.qualified_name = ? COLLATE NOCASE)"
            )
            parameters: tuple[object, ...] = (project_id, query, query, limit)
        else:
            clause = (
                "WHERE s.project_id = ? AND "
                "(s.name LIKE ? ESCAPE '\\' OR s.qualified_name LIKE ? ESCAPE '\\')"
            )
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters = (project_id, f"%{escaped}%", f"%{escaped}%", limit)
        return self._read_symbols(
            f"{clause} ORDER BY CASE WHEN s.name = ? COLLATE NOCASE "
            "THEN 0 ELSE 1 END, s.name LIMIT ?"
            if not exact
            else f"{clause} ORDER BY s.name LIMIT ?",
            (*parameters[:-1], query, parameters[-1]) if not exact else parameters,
        )

    def find_symbols_by_ids(
        self,
        project_id: str,
        symbol_ids: tuple[str, ...],
    ) -> tuple[StructuralSearchResult, ...]:
        if not symbol_ids:
            return ()
        placeholders = ", ".join("?" for _ in symbol_ids)
        return self._read_symbols(
            f"WHERE s.project_id = ? AND s.symbol_id IN ({placeholders}) "
            "ORDER BY f.path, s.start_line",
            (project_id, *symbol_ids),
        )

    def list_symbols(
        self,
        project_id: str,
        paths: tuple[str, ...],
        *,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]:
        if not paths:
            return ()
        placeholders = ", ".join("?" for _ in paths)
        return self._read_symbols(
            f"WHERE s.project_id = ? AND f.path IN ({placeholders}) "
            "ORDER BY f.path, s.start_line LIMIT ?",
            (project_id, *paths, limit),
        )

    def find_references(
        self,
        project_id: str,
        target_name: str,
        *,
        limit: int,
    ) -> tuple[StructuralSearchResult, ...]:
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT r.*, f.path, f.content_hash
                    FROM code_references r JOIN files f ON f.file_id = r.file_id
                    WHERE r.project_id = ? AND r.target_name = ? COLLATE NOCASE
                    ORDER BY f.path, r.start_line LIMIT ?
                    """,
                    (project_id, target_name, limit),
                ).fetchall()
            return tuple(_reference_result(row) for row in rows)
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not query structural references.") from error

    def _read_symbols(
        self, clause: str, parameters: tuple[object, ...]
    ) -> tuple[StructuralSearchResult, ...]:
        try:
            with connect_database(self.path) as connection:
                rows = connection.execute(
                    f"""
                    SELECT s.*, f.path, f.content_hash
                    FROM symbols s JOIN files f ON f.file_id = s.file_id
                    {clause}
                    """,
                    parameters,
                ).fetchall()
            return tuple(_symbol_result(row) for row in rows)
        except sqlite3.DatabaseError as error:
            raise self._corrupted("Could not query structural symbols.") from error

    @staticmethod
    def _count(connection: sqlite3.Connection, table: str, project_id: str) -> int:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )

    def _corrupted(self, message: str) -> IndexCorruptedError:
        return IndexCorruptedError(message, path=str(self.path))


def _duration_ms(started_at: str, finished_at: str) -> int:
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return max(0, round((finished - started).total_seconds() * 1000))


def _run_summary(row: sqlite3.Row) -> IndexRunSummary:
    return IndexRunSummary(
        mode=IndexMode(row["mode"]),
        state=IndexState(row["state"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        discovered_files=row["discovered_files"],
        indexed_files=row["indexed_files"],
        unchanged_files=row["unchanged_files"],
        warning_files=row["warning_files"],
        generated_embeddings=row["generated_embeddings"],
        reused_embeddings=row["reused_embeddings"],
    )


def _identity_values(identity: EmbeddingIdentity) -> tuple[object, ...]:
    return (
        identity.provider,
        identity.provider_version,
        identity.model_id,
        identity.dimensions,
        identity.strategy,
    )


def _pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return (
            bool(get_exit_code(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        )
    finally:
        close_handle(handle)


def _validated_vector(vector: Vector, dimensions: int) -> Vector:
    if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
        raise ValueError("vector dimensions or values are invalid")
    return vector


def _pack_vector(vector: Vector, dimensions: int) -> bytes:
    valid = _validated_vector(vector, dimensions)
    return struct.pack(f"<{dimensions}f", *valid)


def _unpack_vector(value: bytes, dimensions: int) -> Vector:
    if len(value) != dimensions * 4:
        raise ValueError("stored vector has an invalid byte length")
    vector = tuple(float(item) for item in struct.unpack(f"<{dimensions}f", value))
    return _validated_vector(vector, dimensions)


def _cosine(left: Vector, right: Vector) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("cosine similarity is undefined for a zero vector")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _embeddable_chunk(row: sqlite3.Row) -> EmbeddableChunk:
    return EmbeddableChunk(
        chunk_id=row["chunk_id"],
        location=CodeLocation(row["path"], row["start_line"], row["end_line"]),
        content=row["content"],
        content_hash=row["content_hash"],
        language=row["language"],
        file_hash=row["file_hash"],
    )


def _row_location(row: sqlite3.Row) -> CodeLocation:
    return CodeLocation(
        row["path"],
        row["start_line"],
        row["end_line"],
        row["start_column"],
        row["end_column"],
    )


def _symbol_result(row: sqlite3.Row) -> StructuralSearchResult:
    keys = set(row.keys())
    symbol = CodeSymbol(
        row["symbol_id"],
        row["name"],
        row["qualified_name"],
        row["kind"],
        _row_location(row),
        row["signature"],
        row["parent_symbol_id"],
        row["canonical_signature"] if "canonical_signature" in keys else None,
    )
    return StructuralSearchResult(symbol, None, "", row["content_hash"])


def _reference_result(row: sqlite3.Row) -> StructuralSearchResult:
    source_symbol_id = row["source_symbol_id"]
    keys = set(row.keys())
    target_symbol_id = row["target_symbol_id"] if "target_symbol_id" in keys else None
    has_target = bool(target_symbol_id)
    reference = CodeReference(
        row["reference_id"],
        row["target_name"],
        row["kind"],
        _row_location(row),
        source_symbol_id,
        source="structural",
        target_symbol_id=target_symbol_id,
        confidence=1.0 if has_target else (0.85 if source_symbol_id else 0.75),
        resolution="symbol_id" if has_target else "name_only",
    )
    return StructuralSearchResult(None, reference, "", row["content_hash"])
