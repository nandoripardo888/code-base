from code_harness.application.dto.requests import SemanticSearchRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import IndexState, MatchType
from code_harness.domain.errors import CodeHarnessError, EmbeddingUnavailableError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore
from code_harness.domain.protocols.vector_index import VectorIndex


class SemanticSearchTool:
    def __init__(
        self,
        project: Project,
        store: RepositoryStore,
        reader: IndexSourceReader,
        provider: EmbeddingProvider | None,
        vector_index: VectorIndex,
    ) -> None:
        self._project = project
        self._store = store
        self._reader = reader
        self._provider = provider
        self._vector_index = vector_index

    def execute(self, request: SemanticSearchRequest) -> ToolResult[tuple[SearchHit, ...]]:
        provider = self._provider
        if provider is None:
            raise EmbeddingUnavailableError(
                "Semantic search is disabled; set CODE_HARNESS_SEMANTIC=1."
            )
        status = self._store.get_status(self._project)
        if status.state not in (IndexState.READY, IndexState.READY_WITH_WARNINGS):
            raise EmbeddingUnavailableError("Semantic index is not ready; run index first.")
        if not status.semantic_schema_ready or status.chunk_count == 0:
            raise EmbeddingUnavailableError("Semantic index has no searchable chunks.")

        def search() -> tuple[tuple[SearchHit, ...], tuple[str, ...], bool]:
            identity = provider.identity
            warnings: list[str] = []
            missing = self._vector_index.list_unembedded_chunks(self._project.project_id, identity)
            if len(missing) >= status.chunk_count:
                raise EmbeddingUnavailableError(
                    f"Semantic index is not ready for model {identity.model_id}; run index."
                )
            if missing:
                warnings.append(
                    f"Semantic index is partial: {len(missing)} chunk(s) need indexing."
                )
            query_vector = provider.embed_query(request.query)
            candidates = self._vector_index.search_vectors(
                self._project.project_id,
                identity,
                query_vector,
                limit=request.max_results + 1,
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
                languages=request.languages,
            )
            sources: dict[str, IndexedSource | CodeHarnessError] = {}
            hits: list[SearchHit] = []
            for candidate in candidates[: request.max_results]:
                chunk = candidate.chunk
                path = chunk.location.path
                if path not in sources:
                    try:
                        sources[path] = self._reader.load(path)
                    except CodeHarnessError as error:
                        sources[path] = error
                source = sources[path]
                if isinstance(source, CodeHarnessError):
                    warnings.append(
                        f"Skipped {path}: current file is unavailable ({source.code.value})."
                    )
                    continue
                if source.content_hash != chunk.file_hash:
                    warnings.append(f"Skipped stale semantic result for {path}; reindex it.")
                    continue
                lines = source.content.splitlines(keepends=True)
                content = "".join(lines[chunk.location.start_line - 1 : chunk.location.end_line])
                hits.append(
                    SearchHit(
                        CodeSnippet(
                            chunk.location,
                            content,
                            source.language,
                            source.content_hash,
                        ),
                        candidate.score,
                        MatchType.SEMANTIC,
                        (),
                        f"Cosine similarity using {identity.model_id}.",
                    )
                )
            return (
                tuple(hits),
                tuple(dict.fromkeys(warnings)),
                len(candidates) > request.max_results,
            )

        result, elapsed_ms = timed(search)
        hits, warnings, truncated = result
        return ToolResult(
            hits,
            elapsed_ms,
            truncated=truncated,
            warnings=warnings,
            index_state=status.state.value,
        )
