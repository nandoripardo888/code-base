from pathlib import Path

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.infrastructure.embeddings.health_cache import SemanticHealthCache
from code_harness.infrastructure.embeddings.native_supervisor import NativeEmbeddingSupervisor


def test_semantic_health_cache_returns_cached_failure(tmp_path: Path) -> None:
    cache = SemanticHealthCache(ttl_seconds=60)
    key = cache.cache_key(
        provider_id="fastembed",
        model_id="model",
        configuration_hash=str(tmp_path),
    )
    cache.put(key, message="CPU does not support X86_V2", remediation="Install wheels.")

    supervisor = NativeEmbeddingSupervisor(
        "model",
        cache_dir=tmp_path,
        health_cache=cache,
        command=("python", "-c", "raise SystemExit(1)"),
    )

    try:
        supervisor.embed_query("hello")
        raised = False
    except EmbeddingUnavailableError as error:
        raised = True
        assert "X86_V2" in error.message
        assert error.remediation is not None

    assert raised
