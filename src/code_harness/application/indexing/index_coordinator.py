from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import isfinite
from time import perf_counter_ns

from code_harness.application.indexing.change_detector import detect_changes
from code_harness.application.indexing.chunk_builder import (
    CHUNKING_VERSION,
    build_chunks,
    textual_fallback,
)
from code_harness.application.indexing.progress import (
    IndexProgressCallback,
    IndexProgressEvent,
    IndexProgressPhase,
)
from code_harness.domain.enums import IndexMode, IndexState
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import (
    FileIndexUpdate,
    IndexedSource,
    IndexReport,
    IndexTimings,
)
from code_harness.domain.models.project import Project
from code_harness.domain.models.semantic import (
    ChunkEmbeddingLink,
    EmbeddableChunk,
    EmbeddingBatch,
    EmbeddingRecord,
)
from code_harness.domain.models.structural import AnalyzeRequest, AnalyzeResult
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider
from code_harness.domain.protocols.file_catalog import FileCatalog
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.structural_analyzer import StructuralAnalyzer
from code_harness.domain.protocols.vector_index import VectorIndex


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _emit(progress: IndexProgressCallback | None, event: IndexProgressEvent) -> None:
    if progress is not None:
        progress(event)


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)


def _elapsed_ms_precise(started_ns: int) -> float:
    return max(0.0, (perf_counter_ns() - started_ns) / 1_000_000.0)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (percentile / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


@dataclass(slots=True)
class _TimingBucket:
    discovery_ms: float = 0.0
    read_hash_ms: float = 0.0
    analysis_ms: float = 0.0
    chunk_build_ms: float = 0.0
    commit_ms: float = 0.0
    embedding_ms: float = 0.0
    file_bytes_total: int = 0
    files_read: int = 0
    analysis_samples_ms: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ParserMetricsDelta:
    processes_created: int = 0
    distinct_pids: int = 0
    restarts: int = 0
    timeouts: int = 0
    spawn_ms: int = 0
    request_ms: int = 0


def _parser_metrics_snapshot(analyzer: StructuralAnalyzer | None) -> _ParserMetricsDelta:
    if analyzer is None:
        return _ParserMetricsDelta()
    snapshot = getattr(analyzer, "metrics_snapshot", None)
    if not callable(snapshot):
        return _ParserMetricsDelta()
    metrics = snapshot()
    return _ParserMetricsDelta(
        processes_created=int(getattr(metrics, "processes_created", 0) or 0),
        distinct_pids=int(getattr(metrics, "distinct_pids", 0) or 0),
        restarts=int(getattr(metrics, "restarts", 0) or 0),
        timeouts=int(getattr(metrics, "timeouts", 0) or 0),
        spawn_ms=int(getattr(metrics, "spawn_ms", 0) or 0),
        request_ms=int(getattr(metrics, "request_ms", 0) or 0),
    )


def _parser_metrics_delta(
    before: _ParserMetricsDelta,
    after: _ParserMetricsDelta,
) -> _ParserMetricsDelta:
    return _ParserMetricsDelta(
        processes_created=max(0, after.processes_created - before.processes_created),
        distinct_pids=max(0, after.distinct_pids - before.distinct_pids),
        restarts=max(0, after.restarts - before.restarts),
        timeouts=max(0, after.timeouts - before.timeouts),
        spawn_ms=max(0, after.spawn_ms - before.spawn_ms),
        request_ms=max(0, after.request_ms - before.request_ms),
    )


def _build_timings(
    bucket: _TimingBucket,
    *,
    total_ms: int,
    parser_delta: _ParserMetricsDelta,
) -> IndexTimings:
    samples = sorted(bucket.analysis_samples_ms)
    analyzed = len(samples)
    avg = (sum(samples) / analyzed) if analyzed else 0.0
    files_per_second = (analyzed / (total_ms / 1000.0)) if total_ms > 0 and analyzed else 0.0
    avg_file_bytes = (
        bucket.file_bytes_total / bucket.files_read if bucket.files_read else 0.0
    )
    # Spawn cost is reported separately; subtract it from wall analysis so phase sums
    # do not double-count one-shot worker startup.
    analysis_ms = max(0, int(round(bucket.analysis_ms)) - parser_delta.spawn_ms)
    return IndexTimings(
        discovery_ms=int(round(bucket.discovery_ms)),
        read_hash_ms=int(round(bucket.read_hash_ms)),
        worker_init_ms=parser_delta.spawn_ms,
        analysis_ms=analysis_ms,
        chunk_build_ms=int(round(bucket.chunk_build_ms)),
        commit_ms=int(round(bucket.commit_ms)),
        embedding_ms=int(round(bucket.embedding_ms)),
        total_ms=total_ms,
        analyzed_files=analyzed,
        files_per_second=round(files_per_second, 3),
        processes_created=parser_delta.processes_created,
        distinct_pids=parser_delta.distinct_pids,
        worker_restarts=parser_delta.restarts,
        worker_timeouts=parser_delta.timeouts,
        avg_file_bytes=round(avg_file_bytes, 1),
        analysis_ms_avg=round(avg, 3),
        analysis_ms_p50=round(_percentile(samples, 50), 3),
        analysis_ms_p95=round(_percentile(samples, 95), 3),
        analysis_ms_p99=round(_percentile(samples, 99), 3),
    )


class IndexCoordinator:
    def __init__(
        self,
        project: Project,
        catalog: FileCatalog,
        reader: IndexSourceReader,
        store: RepositoryStore,
        *,
        analyzer: StructuralAnalyzer | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_index: VectorIndex | None = None,
        chunk_target_chars: int = 4_000,
        chunk_max_chars: int = 8_000,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._project = project
        self._catalog = catalog
        self._reader = reader
        self._store = store
        self._analyzer = analyzer
        self._embedding_provider = embedding_provider
        self._vector_index = vector_index
        self._chunk_target_chars = chunk_target_chars
        self._chunk_max_chars = chunk_max_chars
        self._clock = clock

    def index(
        self,
        mode: IndexMode,
        *,
        progress: IndexProgressCallback | None = None,
    ) -> IndexReport:
        self._store.initialize(self._project)
        started = self._clock()
        total_started = perf_counter_ns()
        timings = _TimingBucket()
        parser_before = _parser_metrics_snapshot(self._analyzer)
        run_id = self._store.start_run(self._project.project_id, mode, started.isoformat())
        try:
            _emit(
                progress,
                IndexProgressEvent(
                    IndexProgressPhase.DISCOVERING,
                    message="Discovering project files",
                ),
            )
            discovery_started = perf_counter_ns()
            discovered = self._catalog.list_files()
            stored = self._store.list_files(self._project.project_id)
            plan = detect_changes(
                discovered,
                stored,
                mode,
                parser_version=self._analyzer.version if self._analyzer else None,
                chunking_version=CHUNKING_VERSION,
            )
            timings.discovery_ms = _elapsed_ms_precise(discovery_started)
            updates: list[FileIndexUpdate] = []
            removed_paths = [item.path for item in plan.removed]
            warnings: list[str] = []
            new_count = 0
            changed_count = 0
            unchanged_count = len(plan.unchanged)
            analyze_total = len(plan.new) + len(plan.candidates)
            analyze_current = 0

            for source_file in plan.new:
                analyze_current += 1
                _emit(
                    progress,
                    IndexProgressEvent(
                        IndexProgressPhase.ANALYZING,
                        current=analyze_current,
                        total=analyze_total,
                        path=source_file.path,
                    ),
                )
                try:
                    source = self._load_source(source_file.path, timings)
                except CodeHarnessError as error:
                    warnings.append(
                        f"Skipped unreadable file {source_file.path}: {error.code.value}."
                    )
                    continue
                new_count += 1
                if mode is not IndexMode.VERIFY:
                    updates.append(self._build_update(source, warnings, timings))

            for source_file, previous in plan.candidates:
                analyze_current += 1
                _emit(
                    progress,
                    IndexProgressEvent(
                        IndexProgressPhase.ANALYZING,
                        current=analyze_current,
                        total=analyze_total,
                        path=source_file.path,
                    ),
                )
                try:
                    source = self._load_source(source_file.path, timings)
                except CodeHarnessError as error:
                    warnings.append(
                        f"Removed stale entry for unreadable file {source_file.path}: "
                        f"{error.code.value}."
                    )
                    if mode is not IndexMode.VERIFY:
                        removed_paths.append(source_file.path)
                    continue
                strategy_changed = (
                    source.language in {"java", "python", "plsql"}
                    and self._analyzer is not None
                    and previous.parser_version != self._analyzer.version
                ) or previous.chunking_version != CHUNKING_VERSION
                if source.content_hash == previous.content_hash and not strategy_changed:
                    unchanged_count += 1
                    if mode is not IndexMode.VERIFY and (
                        source.size_bytes != previous.size_bytes
                        or source.modified_at_ns != previous.modified_at_ns
                    ):
                        updates.append(FileIndexUpdate(source, update_content=False))
                else:
                    if source.content_hash != previous.content_hash:
                        changed_count += 1
                    if mode is not IndexMode.VERIFY:
                        updates.append(self._build_update(source, warnings, timings))

            if mode is IndexMode.VERIFY and (
                new_count or changed_count or plan.removed or warnings
            ):
                warnings.append("Index verification found differences from the working tree.")

            if self._embedding_provider is not None and self._vector_index is not None:
                _emit(
                    progress,
                    IndexProgressEvent(
                        IndexProgressPhase.EMBEDDING,
                        message="Preparing semantic embeddings",
                    ),
                )
            embedding_started = perf_counter_ns()
            embedding_batch, embedding_failures = self._prepare_embeddings(
                updates,
                tuple(dict.fromkeys(removed_paths)),
                mode,
                warnings,
            )
            timings.embedding_ms = _elapsed_ms_precise(embedding_started)
            finished = self._clock()
            state = IndexState.READY_WITH_WARNINGS if warnings else IndexState.READY
            report = IndexReport(
                project_id=self._project.project_id,
                mode=mode,
                state=state,
                discovered_files=len(discovered),
                new_files=new_count,
                changed_files=changed_count,
                removed_files=len(plan.removed),
                unchanged_files=unchanged_count,
                indexed_files=sum(item.update_content for item in updates),
                warning_files=len(warnings),
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                warnings=tuple(warnings),
                indexed_symbols=sum(
                    len(item.analysis.symbols) for item in updates if item.analysis is not None
                ),
                indexed_references=sum(
                    len(item.analysis.references) for item in updates if item.analysis is not None
                ),
                indexed_chunks=sum(
                    len(item.analysis.chunks) for item in updates if item.analysis is not None
                ),
                parser_failures=sum(
                    item.analysis is not None and item.analysis.state.value == "failed"
                    for item in updates
                ),
                generated_embeddings=embedding_batch.generated_count,
                reused_embeddings=embedding_batch.reused_count,
                embedded_chunks=len(embedding_batch.links),
                embedding_failures=embedding_failures,
            )
            _emit(
                progress,
                IndexProgressEvent(
                    IndexProgressPhase.COMMITTING,
                    message="Writing index to disk",
                ),
            )
            commit_started = perf_counter_ns()
            self._store.commit_files(report, tuple(updates), tuple(dict.fromkeys(removed_paths)))
            try:
                self._store.commit_embeddings(embedding_batch)
            except Exception as error:
                code = error.code.value if isinstance(error, CodeHarnessError) else "storage_error"
                semantic_warning = f"Semantic persistence unavailable ({code}): {error}"
                report = replace(
                    report,
                    state=IndexState.READY_WITH_WARNINGS,
                    warnings=(*report.warnings, semantic_warning),
                    warning_files=len(report.warnings) + 1,
                    generated_embeddings=0,
                    reused_embeddings=0,
                    embedded_chunks=0,
                    embedding_failures=report.embedding_failures + 1,
                )
            self._store.complete_run(run_id, report)
            timings.commit_ms = _elapsed_ms_precise(commit_started)
            parser_delta = _parser_metrics_delta(
                parser_before,
                _parser_metrics_snapshot(self._analyzer),
            )
            report = replace(
                report,
                timings=_build_timings(
                    timings,
                    total_ms=_elapsed_ms(total_started),
                    parser_delta=parser_delta,
                ),
            )
            _emit(
                progress,
                IndexProgressEvent(
                    IndexProgressPhase.COMPLETE,
                    current=analyze_total,
                    total=analyze_total,
                    message="Indexing complete",
                ),
            )
            return report
        except Exception as error:
            self._store.fail_run(run_id, self._clock().isoformat(), str(error))
            raise

    def _load_source(self, path: str, timings: _TimingBucket) -> IndexedSource:
        read_started = perf_counter_ns()
        try:
            source = self._reader.load(path)
        except CodeHarnessError:
            timings.read_hash_ms += _elapsed_ms_precise(read_started)
            raise
        timings.read_hash_ms += _elapsed_ms_precise(read_started)
        timings.file_bytes_total += source.size_bytes
        timings.files_read += 1
        return source

    def _build_update(
        self,
        indexed_source: IndexedSource,
        warnings: list[str],
        timings: _TimingBucket,
    ) -> FileIndexUpdate:
        analyze_started = perf_counter_ns()
        if self._analyzer is None or not self._analyzer.supports(indexed_source.language or ""):
            analysis = textual_fallback(indexed_source)
        else:
            request = AnalyzeRequest(
                request_id=f"{indexed_source.path}:{indexed_source.content_hash[:12]}",
                path=indexed_source.path,
                language=indexed_source.language or "text",
                content=indexed_source.content,
                content_hash=indexed_source.content_hash,
            )
            try:
                analysis = self._analyzer.analyze(request)
                analysis = replace(analysis, parser_version=self._analyzer.version)
                if analysis.warnings:
                    warnings.append(
                        f"Structural analysis warning for {indexed_source.path}: "
                        f"{analysis.warnings[0]}"
                    )
            except CodeHarnessError as error:
                message = f"{error.code.value}: {error.message}"
                warnings.append(f"Structural analysis failed for {indexed_source.path}: {message}")
                analysis = textual_fallback(indexed_source, message)
                analysis = replace(analysis, parser_version=self._analyzer.version)
        analysis_ms = _elapsed_ms_precise(analyze_started)
        timings.analysis_ms += analysis_ms
        timings.analysis_samples_ms.append(analysis_ms)

        chunk_started = perf_counter_ns()
        analysis = build_chunks(
            indexed_source,
            analysis,
            target_chars=self._chunk_target_chars,
            max_chars=self._chunk_max_chars,
        )
        try:
            _validate_analysis(indexed_source, analysis)
        except ValueError as error:
            message = f"invalid_structure: {error}"
            warnings.append(f"Structural analysis failed for {indexed_source.path}: {message}")
            analysis = textual_fallback(indexed_source, message)
        timings.chunk_build_ms += _elapsed_ms_precise(chunk_started)
        return FileIndexUpdate(
            indexed_source,
            analysis=analysis,
            chunking_version=CHUNKING_VERSION,
        )

    def _prepare_embeddings(
        self,
        updates: list[FileIndexUpdate],
        removed_paths: tuple[str, ...],
        mode: IndexMode,
        warnings: list[str],
    ) -> tuple[EmbeddingBatch, int]:
        provider = self._embedding_provider
        vector_index = self._vector_index
        if provider is None or vector_index is None:
            return EmbeddingBatch(), 0
        try:
            identity = provider.identity
            replaced_paths = {
                update.source.path for update in updates if update.update_content
            } | set(removed_paths)
            chunks: list[EmbeddableChunk] = [
                chunk
                for chunk in vector_index.list_unembedded_chunks(self._project.project_id, identity)
                if chunk.location.path not in replaced_paths
            ]
            for update in updates:
                if not update.update_content or update.analysis is None:
                    continue
                chunks.extend(
                    EmbeddableChunk(
                        chunk.chunk_id,
                        CodeLocation(
                            update.source.path,
                            chunk.location.start_line,
                            chunk.location.end_line,
                        ),
                        chunk.content,
                        chunk.content_hash,
                        update.source.language,
                        update.source.content_hash,
                    )
                    for chunk in update.analysis.chunks
                )
            chunks = list({chunk.chunk_id: chunk for chunk in chunks}.values())
            if not chunks:
                return EmbeddingBatch(identity=identity), 0
            if mode is IndexMode.VERIFY:
                warnings.append(
                    f"Semantic verification found {len(chunks)} chunk(s) without "
                    f"embeddings for {identity.model_id}."
                )
                return EmbeddingBatch(identity=identity), 0
            hashes = tuple(dict.fromkeys(chunk.content_hash for chunk in chunks))
            cached = vector_index.get_cached_embeddings(identity, hashes)
            cached_by_hash = {record.content_hash: record for record in cached}
            content_by_hash = {
                chunk.content_hash: chunk.content
                for chunk in chunks
                if chunk.content_hash not in cached_by_hash
            }
            generated: list[EmbeddingRecord] = []
            if content_by_hash:
                content_hashes = tuple(content_by_hash)
                vectors = provider.embed_documents(
                    tuple(content_by_hash[item] for item in content_hashes)
                )
                if len(vectors) != len(content_hashes):
                    raise ValueError("embedding provider returned an unexpected vector count")
                if any(
                    len(vector) != identity.dimensions
                    or not all(isfinite(value) for value in vector)
                    for vector in vectors
                ):
                    raise ValueError("embedding provider returned invalid dimensions or values")
                generated_at = self._clock().isoformat()
                generated = [
                    EmbeddingRecord(identity, content_hash, vector, generated_at)
                    for content_hash, vector in zip(content_hashes, vectors, strict=True)
                ]
            links = tuple(
                ChunkEmbeddingLink(chunk.chunk_id, chunk.content_hash) for chunk in chunks
            )
            reused_count = len(chunks) - len(generated)
            return (
                EmbeddingBatch(
                    identity=identity,
                    records=tuple(generated),
                    links=links,
                    generated_count=len(generated),
                    reused_count=reused_count,
                ),
                0,
            )
        except Exception as error:
            code = error.code.value if isinstance(error, CodeHarnessError) else "invalid_embedding"
            warnings.append(f"Semantic indexing unavailable ({code}): {error}")
            return EmbeddingBatch(), 1


def _validate_analysis(indexed_source: IndexedSource, analysis: AnalyzeResult) -> None:
    # Validate before entering the database transaction so one malformed optional
    # parser result can degrade to textual chunks instead of aborting the project.
    for collection_name in ("symbols", "references", "chunks"):
        collection = getattr(analysis, collection_name)
        identifier_name = {
            "symbols": "symbol_id",
            "references": "reference_id",
            "chunks": "chunk_id",
        }[collection_name]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in collection:
            identifier = str(getattr(item, identifier_name))
            if identifier in seen:
                duplicates.add(identifier)
            seen.add(identifier)
            if item.location.path != indexed_source.path:
                raise ValueError(
                    f"{collection_name} contains a location outside {indexed_source.path}"
                )
        if duplicates:
            sample = ", ".join(sorted(duplicates)[:3])
            raise ValueError(f"duplicate {identifier_name} value(s): {sample}")
