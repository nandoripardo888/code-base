import os
import sys
from types import SimpleNamespace

import pytest

from code_harness.application.tools.prepare_semantic_model import PrepareSemanticModelTool
from code_harness.bootstrap.tls import configure_application_tls
from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.infrastructure.embeddings import FakeEmbeddingProvider


def test_prepare_semantic_model_runs_real_provider_probe(tmp_path) -> None:
    provider = FakeEmbeddingProvider()
    result = PrepareSemanticModelTool(provider, tmp_path / "models").execute()

    assert result.data.ready
    assert result.data.dimensions == provider.identity.dimensions
    assert result.data.cache_path == str(tmp_path / "models")
    assert provider.query_calls == ["code harness semantic readiness probe"]
    assert len(provider.document_calls) == 1
    assert len(provider.document_calls[0]) == 18
    assert "índice semântico" in provider.document_calls[0][0]


def test_prepare_semantic_model_requires_provider(tmp_path) -> None:
    with pytest.raises(EmbeddingUnavailableError, match="disabled"):
        PrepareSemanticModelTool(None, tmp_path).execute()


def test_application_tls_uses_system_store_and_explicit_ca(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    fake = SimpleNamespace(inject_into_ssl=lambda: calls.append(True))
    monkeypatch.setitem(sys.modules, "truststore", fake)
    bundle = tmp_path / "company.pem"

    assert configure_application_tls(ca_bundle_path=bundle) is None
    assert calls == [True]
    assert os.environ["SSL_CERT_FILE"] == str(bundle)


def test_application_tls_can_skip_system_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert configure_application_tls(use_system_trust=False) is None
