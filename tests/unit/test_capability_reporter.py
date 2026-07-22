from code_harness.domain.enums import CapabilityState, IndexState
from code_harness.domain.models.index_report import IndexStatus
from code_harness.infrastructure.diagnostics.capability_reporter import LocalCapabilityReporter
from code_harness.infrastructure.embeddings.health_cache import SemanticHealthCache


def _status(*, embedding_count: int = 0, semantic_ready: bool = False) -> IndexStatus:
    return IndexStatus(
        project_id="p",
        state=IndexState.READY_WITH_WARNINGS,
        schema_version=1,
        file_count=1,
        fts_document_count=1,
        warning_files=0,
        structural_schema_ready=True,
        semantic_schema_ready=semantic_ready,
        embedding_count=embedding_count,
    )


def test_semantic_capability_unavailable_when_embeddings_missing() -> None:
    reporter = LocalCapabilityReporter(
        semantic_enabled=True,
        semantic_model_id="model",
    )

    capabilities = {item.name: item for item in reporter.report(_status())}

    assert capabilities["semantic"].state is CapabilityState.UNAVAILABLE
    assert capabilities["semantic"].root_cause == "embedding_unavailable"
    assert capabilities["semantic"].remediation is not None


def test_semantic_capability_respects_cached_provider_failure(tmp_path) -> None:
    cache = SemanticHealthCache(ttl_seconds=60)
    key = cache.cache_key(
        provider_id="fastembed",
        model_id="model",
        configuration_hash=str(tmp_path),
    )
    cache.put(
        key,
        message="embedding_unavailable",
        remediation="Install a NumPy/runtime build compatible with this CPU.",
    )
    reporter = LocalCapabilityReporter(
        semantic_enabled=True,
        semantic_model_id="model",
        model_cache_path=tmp_path,
        health_cache=cache,
    )

    capabilities = {
        item.name: item
        for item in reporter.report(_status(embedding_count=10, semantic_ready=True))
    }

    assert capabilities["semantic"].state is CapabilityState.UNAVAILABLE
    assert capabilities["semantic"].root_cause == "embedding_unavailable"
