from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.enums import CapabilityState, IndexState
from code_harness.domain.models.capability import CapabilityStatus
from code_harness.domain.models.index_report import IndexStatus
from code_harness.infrastructure.embeddings.health_cache import (
    DEFAULT_SEMANTIC_HEALTH_CACHE,
    SemanticHealthCache,
)
from code_harness.infrastructure.ripgrep.discovery import probe_ripgrep


class LocalCapabilityReporter:
    def __init__(
        self,
        *,
        semantic_enabled: bool = False,
        semantic_model_id: str | None = None,
        ripgrep_executable: str = "rg",
        model_cache_path: Path | None = None,
        health_cache: SemanticHealthCache | None = None,
    ) -> None:
        self._semantic_enabled = semantic_enabled
        self._semantic_model_id = semantic_model_id
        self._ripgrep_executable = ripgrep_executable
        self._model_cache_path = model_cache_path
        self._health_cache = health_cache or DEFAULT_SEMANTIC_HEALTH_CACHE

    def report(self, status: IndexStatus) -> tuple[CapabilityStatus, ...]:
        checked_at = datetime.now(UTC)
        index_ready = status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
        ripgrep = probe_ripgrep(self._ripgrep_executable)
        ripgrep_state = (
            CapabilityState.READY
            if ripgrep.execution_test == "passed"
            else CapabilityState.UNAVAILABLE
        )
        semantic_state, semantic_cause, semantic_remediation = self._semantic_capability(
            status
        )
        return (
            CapabilityStatus(
                "filesystem",
                CapabilityState.READY,
                optional=False,
                enabled=True,
                last_health_check=checked_at,
            ),
            CapabilityStatus(
                "catalog",
                CapabilityState.READY if index_ready else CapabilityState.UNAVAILABLE,
                optional=False,
                enabled=True,
                last_health_check=checked_at,
            ),
            CapabilityStatus(
                "fts",
                CapabilityState.READY if index_ready else CapabilityState.UNAVAILABLE,
                optional=False,
                enabled=True,
                last_health_check=checked_at,
            ),
            CapabilityStatus(
                "structural",
                CapabilityState.READY
                if index_ready and status.structural_schema_ready
                else CapabilityState.UNAVAILABLE,
                optional=False,
                enabled=True,
                last_health_check=checked_at,
            ),
            CapabilityStatus(
                "ripgrep",
                ripgrep_state,
                optional=False,
                enabled=True,
                root_cause=ripgrep.root_cause,
                remediation="; ".join(ripgrep.remediation) if ripgrep.remediation else None,
                last_health_check=checked_at,
                metadata={
                    "resolved_path": ripgrep.resolved_path,
                    "version": ripgrep.version,
                },
            ),
            CapabilityStatus(
                "semantic",
                semantic_state,
                optional=True,
                enabled=self._semantic_enabled,
                root_cause=semantic_cause,
                remediation=semantic_remediation,
                last_health_check=checked_at,
                metadata={
                    "model_id": self._semantic_model_id,
                    "embedding_count": status.embedding_count,
                },
            ),
        )

    def invalidate_semantic_health(self) -> None:
        key = self._health_cache.cache_key(
            provider_id="fastembed",
            model_id=self._semantic_model_id or "unknown",
            configuration_hash=str(self._model_cache_path or ""),
        )
        self._health_cache.invalidate(key)

    def _semantic_capability(
        self,
        status: IndexStatus,
    ) -> tuple[CapabilityState, str | None, str | None]:
        if not self._semantic_enabled:
            return CapabilityState.DISABLED, None, None
        key = self._health_cache.cache_key(
            provider_id="fastembed",
            model_id=self._semantic_model_id or "unknown",
            configuration_hash=str(self._model_cache_path or ""),
        )
        cached = self._health_cache.get(key)
        if cached is not None:
            return (
                CapabilityState.UNAVAILABLE,
                cached.message or "embedding_unavailable",
                cached.remediation
                or "Install a NumPy/runtime build compatible with this CPU.",
            )
        if not self._semantic_model_id:
            return CapabilityState.UNKNOWN, "Semantic model is not configured.", None
        if not status.semantic_schema_ready or status.embedding_count <= 0:
            return (
                CapabilityState.UNAVAILABLE,
                "embedding_unavailable",
                "Install a NumPy/runtime build compatible with this CPU.",
            )
        return CapabilityState.READY, None, None
