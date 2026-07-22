import json
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import EmbeddingIdentity, Vector
from code_harness.infrastructure.embeddings.health_cache import (
    DEFAULT_SEMANTIC_HEALTH_CACHE,
    SemanticHealthCache,
)


class NativeEmbeddingSupervisor:
    """Runs model loading and inference outside the CLI/indexing process."""

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 16,
        window_chars: int = 1_500,
        window_overlap_chars: int = 150,
        cache_dir: Path,
        timeout_seconds: float = 300.0,
        system_trust: bool = True,
        ca_bundle_path: Path | None = None,
        command: Sequence[str] | None = None,
        health_cache: SemanticHealthCache | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._window_chars = window_chars
        self._window_overlap_chars = window_overlap_chars
        self._cache_dir = cache_dir
        self._timeout_seconds = timeout_seconds
        self._system_trust = system_trust
        self._ca_bundle_path = ca_bundle_path
        self._command = tuple(
            command
            or (
                sys.executable,
                "-m",
                "code_harness.infrastructure.embeddings.native_worker",
            )
        )
        self._identity: EmbeddingIdentity | None = None
        self._active: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._health_cache = health_cache or DEFAULT_SEMANTIC_HEALTH_CACHE
        self._cache_key = self._health_cache.cache_key(
            provider_id="fastembed",
            model_id=model_name,
            configuration_hash=str(cache_dir),
        )

    def invalidate_health_cache(self) -> None:
        self._health_cache.invalidate(self._cache_key)

    @property
    def identity(self) -> EmbeddingIdentity:
        if self._identity is None:
            payload = self._run("identity")
            raw = payload.get("identity")
            if not isinstance(raw, dict):
                raise EmbeddingUnavailableError("Embedding worker returned no model identity.")
            try:
                self._identity = EmbeddingIdentity(
                    provider=str(raw["provider"]),
                    provider_version=str(raw["provider_version"]),
                    model_id=str(raw["model_id"]),
                    dimensions=int(raw["dimensions"]),
                    strategy=str(raw["strategy"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise EmbeddingUnavailableError(
                    "Embedding worker returned an invalid model identity."
                ) from error
        return self._identity

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        if not texts:
            return ()
        payload = self._run("embed_documents", texts=list(texts))
        vectors = payload.get("vectors")
        if not isinstance(vectors, list):
            raise EmbeddingUnavailableError("Embedding worker returned invalid document vectors.")
        return tuple(_vector(item) for item in vectors)

    def embed_query(self, text: str) -> Vector:
        payload = self._run("embed_query", text=text)
        return _vector(payload.get("vector"))

    def shutdown(self) -> None:
        with self._lock:
            process = self._active
            self._active = None
        if process is not None and process.poll() is None:
            _stop_process(process)

    def _run(self, operation: str, **values: object) -> dict[str, Any]:
        cached = self._health_cache.get(self._cache_key)
        if cached is not None:
            raise EmbeddingUnavailableError(
                cached.message,
                remediation=cached.remediation
                or "Install compatible NumPy/ONNX Runtime wheels or disable semantic search.",
            )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "operation": operation,
            "model": self._model_name,
            "batch_size": self._batch_size,
            "window_chars": self._window_chars,
            "window_overlap_chars": self._window_overlap_chars,
            "cache_dir": str(self._cache_dir),
            "system_trust": self._system_trust,
            "ca_bundle": str(self._ca_bundle_path) if self._ca_bundle_path else None,
            **values,
        }
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except OSError as error:
            message = f"Could not start embedding worker: {error}"
            self._remember_failure(message)
            raise EmbeddingUnavailableError(message) from error
        with self._lock:
            self._active = process
        try:
            try:
                stdout, stderr = process.communicate(
                    # Keep the JSON wire format ASCII-only. On Windows a child Python
                    # process can inherit a legacy console code page (for example
                    # cp1252) even though this side of the pipe is UTF-8. Escaping
                    # non-ASCII characters makes the protocol independent of that
                    # ambient encoding and json.loads restores the original text.
                    json.dumps(payload, ensure_ascii=True),
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                _stop_process(process)
                message = (
                    f"Embedding worker timed out after {self._timeout_seconds:g} seconds."
                )
                self._remember_failure(message)
                raise EmbeddingUnavailableError(message) from error
        finally:
            with self._lock:
                if self._active is process:
                    self._active = None
        if process.returncode != 0:
            detail = stderr.strip() or f"exit code {process.returncode}"
            if "OPENSSL_Applink" in detail:
                detail = (
                    "the Python/OpenSSL runtime is incompatible; recreate the virtual "
                    "environment with an official Python installation"
                )
            message = f"Embedding worker crashed: {detail}"
            self._remember_failure(message)
            raise EmbeddingUnavailableError(message)
        try:
            decoded = json.loads(stdout)
        except json.JSONDecodeError as error:
            message = "Embedding worker returned invalid JSON."
            self._remember_failure(message)
            raise EmbeddingUnavailableError(message) from error
        if not isinstance(decoded, dict):
            message = "Embedding worker returned an invalid response."
            self._remember_failure(message)
            raise EmbeddingUnavailableError(message)
        worker_error = decoded.get("error")
        if isinstance(worker_error, dict):
            message = str(worker_error.get("message", "Worker failed."))
            self._remember_failure(message)
            raise EmbeddingUnavailableError(message)
        return decoded

    def _remember_failure(self, message: str) -> None:
        remediation = (
            "Install compatible NumPy/ONNX Runtime wheels or disable semantic search."
        )
        if "X86_V2" in message or "onnxruntime" in message.casefold():
            remediation = (
                "Install CPU-compatible NumPy/ONNX Runtime wheels for this machine, "
                "or disable CODE_HARNESS_SEMANTIC."
            )
        self._health_cache.put(
            self._cache_key,
            message=message,
            remediation=remediation,
        )


def _vector(value: object) -> Vector:
    if not isinstance(value, list):
        raise EmbeddingUnavailableError("Embedding worker returned an invalid vector.")
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError) as error:
        raise EmbeddingUnavailableError("Embedding worker returned non-numeric values.") from error


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)
