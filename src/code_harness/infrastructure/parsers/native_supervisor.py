import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from code_harness.domain.enums import ParseState
from code_harness.domain.errors import (
    ParserCircuitOpenError,
    ParserCrashError,
    ParserTimeoutError,
    ParserUnavailableError,
)
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.structural import (
    AnalyzeRequest,
    AnalyzeResult,
    CodeChunk,
    CodeReference,
    CodeSymbol,
)


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class NativeParserSupervisor:
    """Runs every parser request in a disposable, supervised child process."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        failure_threshold: int = 3,
        circuit_reset_seconds: float = 60.0,
        command: Sequence[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._failure_threshold = failure_threshold
        self._circuit_reset_seconds = circuit_reset_seconds
        self._command = tuple(
            command
            or (
                sys.executable,
                "-m",
                "code_harness.infrastructure.parsers.native_worker",
            )
        )
        self._clock = clock
        self._circuits: dict[str, _CircuitState] = {}
        self._problem_payloads: set[tuple[str, str, str]] = set()
        self._active: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "native-parser-supervisor"

    @property
    def version(self) -> str:
        return "2"

    def supports(self, language: str) -> bool:
        return self._enabled and language.casefold() in {"java", "python", "plsql"}

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        language = request.language.casefold()
        if not self.supports(language):
            raise ParserUnavailableError(request.language)
        key = (language, request.path, request.content_hash)
        if key in self._problem_payloads:
            raise ParserCrashError(request.path, "Parser skipped a previously failing payload.")
        self._check_circuit(language)
        try:
            payload = self._run(
                {
                    "operation": "analyze",
                    "request_id": request.request_id,
                    "path": request.path,
                    "language": language,
                    "content": request.content,
                    "content_hash": request.content_hash,
                },
                request.path,
            )
            result = _decode_result(payload)
        except (ParserCrashError, ParserTimeoutError):
            self._problem_payloads.add(key)
            self._record_failure(language)
            raise
        self._circuits.pop(language, None)
        return result

    def health_check(self) -> bool:
        if not self._enabled:
            return True
        try:
            response = self._run({"operation": "health"}, "<health-check>")
        except (ParserCrashError, ParserTimeoutError):
            return False
        return response.get("status") == "ok"

    def shutdown(self) -> None:
        with self._lock:
            process = self._active
            self._active = None
        if process is not None and process.poll() is None:
            _stop_process(process)

    def _run(self, payload: dict[str, object], path: str) -> dict[str, Any]:
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
            raise ParserCrashError(path, f"Could not start parser worker: {error}") from error
        with self._lock:
            self._active = process
        try:
            try:
                stdout, stderr = process.communicate(
                    # JSON escapes make source text round-trip independently of the
                    # Windows console code page inherited by the child process.
                    json.dumps(payload, ensure_ascii=True),
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                _stop_process(process)
                raise ParserTimeoutError(path, self._timeout_seconds) from error
        finally:
            with self._lock:
                if self._active is process:
                    self._active = None
        if process.returncode != 0:
            message = stderr.strip() or f"Parser worker exited with code {process.returncode}."
            raise ParserCrashError(path, message)
        try:
            decoded = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ParserCrashError(path, "Parser worker returned invalid JSON.") from error
        if not isinstance(decoded, dict):
            raise ParserCrashError(path, "Parser worker returned an invalid response.")
        worker_error = decoded.get("error")
        if worker_error:
            raise ParserCrashError(path, str(worker_error))
        return decoded

    def _check_circuit(self, language: str) -> None:
        state = self._circuits.get(language)
        if state is None or state.opened_at is None:
            return
        if self._clock() - state.opened_at >= self._circuit_reset_seconds:
            self._circuits.pop(language, None)
            return
        raise ParserCircuitOpenError(language)

    def _record_failure(self, language: str) -> None:
        state = self._circuits.setdefault(language, _CircuitState())
        state.failures += 1
        if state.failures >= self._failure_threshold:
            state.opened_at = self._clock()


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def _location(payload: dict[str, Any]) -> CodeLocation:
    return CodeLocation(
        str(payload["path"]),
        int(payload["start_line"]),
        int(payload["end_line"]),
        int(payload["start_column"]) if payload.get("start_column") is not None else None,
        int(payload["end_column"]) if payload.get("end_column") is not None else None,
    )


def _decode_result(payload: dict[str, Any]) -> AnalyzeResult:
    symbols = tuple(
        CodeSymbol(
            symbol_id=str(item["symbol_id"]),
            name=str(item["name"]),
            qualified_name=(str(item["qualified_name"]) if item.get("qualified_name") else None),
            kind=str(item["kind"]),
            location=_location(item["location"]),
            signature=str(item["signature"]) if item.get("signature") else None,
            parent_symbol_id=(
                str(item["parent_symbol_id"]) if item.get("parent_symbol_id") else None
            ),
        )
        for item in payload.get("symbols", ())
    )
    references = tuple(
        CodeReference(
            reference_id=str(item["reference_id"]),
            target_name=str(item["target_name"]),
            kind=str(item["kind"]),
            location=_location(item["location"]),
            source_symbol_id=(
                str(item["source_symbol_id"]) if item.get("source_symbol_id") else None
            ),
        )
        for item in payload.get("references", ())
    )
    chunks = tuple(
        CodeChunk(
            chunk_id=str(item["chunk_id"]),
            location=_location(item["location"]),
            content=str(item["content"]),
            content_hash=str(item["content_hash"]),
            kind=str(item["kind"]),
            symbol_id=str(item["symbol_id"]) if item.get("symbol_id") else None,
            parent_chunk_id=(str(item["parent_chunk_id"]) if item.get("parent_chunk_id") else None),
        )
        for item in payload.get("chunks", ())
    )
    return AnalyzeResult(
        parser_name=str(payload["parser_name"]),
        parser_version=str(payload["parser_version"]),
        state=ParseState(str(payload["state"])),
        symbols=symbols,
        references=references,
        chunks=chunks,
        warnings=tuple(str(item) for item in payload.get("warnings", ())),
    )
