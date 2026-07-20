from math import isclose

import pytest

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import EmbeddingIdentity
from code_harness.infrastructure.embeddings.fastembed_provider import FastEmbedProvider


class StubModel:
    def passage_embed(self, texts: tuple[str, ...], *, batch_size: int):
        return ([1.0, float(index + 1)] for index, _ in enumerate(texts))

    def query_embed(self, texts: tuple[str, ...], *, batch_size: int):
        return ([1.0, 1.0] for _ in texts)


def test_fastembed_provider_windows_averages_and_normalizes() -> None:
    provider = FastEmbedProvider("stub", window_chars=10, window_overlap_chars=2)
    provider._model = StubModel()
    provider._identity = EmbeddingIdentity("fastembed", "test", "stub", 2, "test")

    vector = provider.embed_documents(("first line\nsecond line\nthird",))[0]
    query = provider.embed_query("question")

    assert len(provider._windows("first line\nsecond line\nthird")) > 1
    assert isclose(sum(value * value for value in vector), 1.0)
    assert isclose(sum(value * value for value in query), 1.0)


def test_fastembed_provider_rejects_wrong_dimensions() -> None:
    provider = FastEmbedProvider("stub")
    provider._model = StubModel()
    provider._identity = EmbeddingIdentity("fastembed", "test", "stub", 3, "test")

    with pytest.raises(EmbeddingUnavailableError):
        provider.embed_query("question")
