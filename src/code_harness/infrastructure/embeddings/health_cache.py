"""In-process cache for known semantic provider failures."""

from __future__ import annotations

import hashlib
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock


@dataclass(frozen=True, slots=True)
class SemanticFailureRecord:
    cache_key: str
    message: str
    remediation: str | None
    recorded_at: datetime


class SemanticHealthCache:
    def __init__(self, *, ttl_seconds: float = 3_600.0) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = Lock()
        self._failures: dict[str, SemanticFailureRecord] = {}

    def cache_key(
        self,
        *,
        provider_id: str,
        model_id: str,
        provider_version: str = "unknown",
        configuration_hash: str = "",
    ) -> str:
        material = "\x1f".join(
            (
                provider_id,
                model_id,
                provider_version,
                sys.version.split()[0],
                platform.platform(),
                platform.machine(),
                configuration_hash,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> SemanticFailureRecord | None:
        with self._lock:
            record = self._failures.get(key)
            if record is None:
                return None
            if datetime.now(UTC) - record.recorded_at > self._ttl:
                self._failures.pop(key, None)
                return None
            return record

    def put(
        self,
        key: str,
        *,
        message: str,
        remediation: str | None = None,
    ) -> SemanticFailureRecord:
        record = SemanticFailureRecord(
            cache_key=key,
            message=message,
            remediation=remediation,
            recorded_at=datetime.now(UTC),
        )
        with self._lock:
            self._failures[key] = record
        return record

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


DEFAULT_SEMANTIC_HEALTH_CACHE = SemanticHealthCache()
