from math import isfinite
from pathlib import Path

from code_harness.application.tools._timing import timed
from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import SemanticPreparationReport
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider


class PrepareSemanticModelTool:
    def __init__(self, provider: EmbeddingProvider | None, cache_path: Path) -> None:
        self._provider = provider
        self._cache_path = cache_path

    def execute(self) -> ToolResult[SemanticPreparationReport]:
        if self._provider is None:
            raise EmbeddingUnavailableError(
                "Semantic search is disabled; set CODE_HARNESS_SEMANTIC=1 first."
            )
        provider = self._provider

        def prepare() -> SemanticPreparationReport:
            identity = provider.identity
            vector = provider.embed_query("code harness semantic readiness probe")
            if len(vector) != identity.dimensions or not all(isfinite(item) for item in vector):
                raise EmbeddingUnavailableError(
                    "Embedding readiness probe returned invalid dimensions or values."
                )
            documents = (
                "índice semântico com ação, validação e Unicode",
                "",
                *(f"semantic batch readiness document {index}" for index in range(16)),
            )
            document_vectors = provider.embed_documents(documents)
            if len(document_vectors) != len(documents) or any(
                len(item) != identity.dimensions or not all(isfinite(value) for value in item)
                for item in document_vectors
            ):
                raise EmbeddingUnavailableError(
                    "Embedding batch readiness probe returned invalid vectors."
                )
            return SemanticPreparationReport(
                ready=True,
                provider=identity.provider,
                provider_version=identity.provider_version,
                model_id=identity.model_id,
                dimensions=identity.dimensions,
                strategy=identity.strategy,
                cache_path=str(self._cache_path),
            )

        report, elapsed_ms = timed(prepare)
        return ToolResult(report, elapsed_ms)
