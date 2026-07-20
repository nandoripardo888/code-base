from collections.abc import Sequence
from importlib import import_module, metadata
from math import isfinite, sqrt
from pathlib import Path
from typing import Any

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import EmbeddingIdentity, Vector


class FastEmbedProvider:
    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 16,
        window_chars: int = 1_500,
        window_overlap_chars: int = 150,
        cache_dir: Path | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._window_chars = window_chars
        self._window_overlap_chars = window_overlap_chars
        self._cache_dir = cache_dir
        self._model: Any | None = None
        self._identity: EmbeddingIdentity | None = None

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._ensure_identity()

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        if not texts:
            return ()
        model = self._ensure_model()
        windows_by_text = tuple(self._windows(text) for text in texts)
        flattened = tuple(window for windows in windows_by_text for window in windows)
        try:
            raw_vectors = tuple(model.passage_embed(flattened, batch_size=self._batch_size))
        except Exception as error:
            raise EmbeddingUnavailableError(
                f"Local embedding inference failed for model {self._model_name}."
            ) from error
        return self._aggregate(raw_vectors, windows_by_text)

    def embed_query(self, text: str) -> Vector:
        model = self._ensure_model()
        windows = self._windows(text)
        try:
            raw_vectors = tuple(model.query_embed(windows, batch_size=self._batch_size))
        except Exception as error:
            raise EmbeddingUnavailableError(
                f"Local query embedding failed for model {self._model_name}."
            ) from error
        aggregated = self._aggregate(raw_vectors, (windows,))
        if not aggregated:
            raise EmbeddingUnavailableError("Embedding provider returned no query vector.")
        return aggregated[0]

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        identity = self._ensure_identity()
        try:
            TextEmbedding = _text_embedding_class()

            model = TextEmbedding(
                model_name=self._model_name,
                cache_dir=str(self._cache_dir) if self._cache_dir is not None else None,
            )
        except (ImportError, metadata.PackageNotFoundError) as error:
            raise EmbeddingUnavailableError(
                "Semantic dependencies are unavailable; install code-harness[semantic]."
            ) from error
        except Exception as error:
            raise EmbeddingUnavailableError(
                f"Could not load or download local embedding model {self._model_name}."
            ) from error
        self._model = model
        self._identity = identity
        return model

    def _ensure_identity(self) -> EmbeddingIdentity:
        if self._identity is not None:
            return self._identity
        try:
            TextEmbedding = _text_embedding_class()

            supported = {
                str(item["model"]): int(item["dim"])
                for item in TextEmbedding.list_supported_models()
            }
            dimensions = supported.get(self._model_name)
            if dimensions is None:
                raise EmbeddingUnavailableError(
                    f"FastEmbed does not support configured model {self._model_name}."
                )
            provider_version = metadata.version("fastembed")
        except EmbeddingUnavailableError:
            raise
        except (ImportError, metadata.PackageNotFoundError) as error:
            raise EmbeddingUnavailableError(
                "Semantic dependencies are unavailable; install code-harness[semantic]."
            ) from error
        except Exception as error:
            raise EmbeddingUnavailableError(
                f"Could not inspect local embedding model {self._model_name}."
            ) from error
        self._identity = EmbeddingIdentity(
            provider="fastembed",
            provider_version=provider_version,
            model_id=self._model_name,
            dimensions=dimensions,
            strategy=(
                f"model-mean-pooling:windowed-mean-l2-v1:"
                f"{self._window_chars}:{self._window_overlap_chars}"
            ),
        )
        return self._identity

    def _windows(self, text: str) -> tuple[str, ...]:
        if not text:
            return ("",)
        windows: list[str] = []
        start = 0
        while start < len(text):
            proposed_end = min(len(text), start + self._window_chars)
            end = proposed_end
            if proposed_end < len(text):
                boundary = text.rfind("\n", start + self._window_chars // 2, proposed_end)
                if boundary > start:
                    end = boundary + 1
            windows.append(text[start:end])
            if end >= len(text):
                break
            start = max(start + 1, end - self._window_overlap_chars)
        return tuple(windows)

    def _aggregate(
        self,
        raw_vectors: Sequence[Any],
        windows_by_text: Sequence[Sequence[str]],
    ) -> tuple[Vector, ...]:
        identity = self.identity
        results: list[Vector] = []
        cursor = 0
        for windows in windows_by_text:
            count = len(windows)
            selected = raw_vectors[cursor : cursor + count]
            cursor += count
            if len(selected) != count:
                raise EmbeddingUnavailableError("Embedding provider returned too few vectors.")
            vectors = tuple(self._validated_vector(item, identity.dimensions) for item in selected)
            averaged = tuple(
                sum(vector[index] for vector in vectors) / count
                for index in range(identity.dimensions)
            )
            results.append(_normalize(averaged))
        if cursor != len(raw_vectors):
            raise EmbeddingUnavailableError("Embedding provider returned too many vectors.")
        return tuple(results)

    @staticmethod
    def _validated_vector(value: Any, dimensions: int) -> Vector:
        try:
            vector = tuple(float(item) for item in value)
        except (TypeError, ValueError) as error:
            raise EmbeddingUnavailableError(
                "Embedding provider returned an invalid vector."
            ) from error
        if len(vector) != dimensions or not all(isfinite(item) for item in vector):
            raise EmbeddingUnavailableError(
                "Embedding provider returned a vector with invalid dimensions or values."
            )
        return vector


def _normalize(vector: Vector) -> Vector:
    norm = sqrt(sum(value * value for value in vector))
    if not isfinite(norm) or norm == 0:
        raise EmbeddingUnavailableError("Embedding provider returned a zero or invalid vector.")
    return tuple(value / norm for value in vector)


def _text_embedding_class() -> Any:
    return import_module("fastembed").TextEmbedding
