"""Supervised native structural parser with a persistent worker pool.

Tree-sitter stays isolated in child processes. Each slot handles one request at a
time; circuit breaker and problem-payload cache live on the supervisor.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from queue import Empty, Queue
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
from code_harness.infrastructure.parsers.native_protocol import (
    ANALYSIS_VERSION,
    ERROR_KIND_ANALYSIS,
    OPERATION_ANALYZE,
    OPERATION_HEALTH,
    OPERATION_SHUTDOWN,
    PROTOCOL_VERSION,
    WORKER_IMPLEMENTATION_VERSION,
    build_request,
    validate_response,
)

_STDERR_MAX_LINES = 50
_STDERR_MAX_BYTES = 32 * 1024
_READER_JOIN_SECONDS = 1.0
_SHUTDOWN_WAIT_SECONDS = 2.0

ProblemPayloadKey = tuple[str, str, str, str, str]


@dataclass(slots=True)
class _CircuitState:
    recent_failures: deque[tuple[float, ProblemPayloadKey]] = field(default_factory=deque)
    opened_at: float | None = None


@dataclass(frozen=True, slots=True)
class ParserRuntimeMetrics:
    processes_created: int = 0
    distinct_pids: int = 0
    restarts: int = 0
    timeouts: int = 0
    spawn_ms: int = 0
    request_ms: int = 0
    parser_cache_hits: int = 0
    worker_count: int = 0
    max_concurrent_analyses: int = 0


class _PersistentWorkerSlot:
    """Owns one worker process; processes a single request at a time."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        slot_id: str = "worker-0",
    ) -> None:
        self._command = tuple(command)
        self._timeout_seconds = timeout_seconds
        self._slot_id = slot_id
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._response_queue: Queue[tuple[str, object | None]] = Queue()
        self._stderr_lines: deque[str] = deque()
        self._stderr_bytes = 0
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._request_counter = 0
        self._ever_started = False
        self._processes_created = 0
        self._pids: set[int] = set()
        self._restarts = 0
        self._timeouts = 0
        self._spawn_ms = 0
        self._request_ms = 0
        self._parser_cache_hits = 0

    @property
    def slot_id(self) -> str:
        return self._slot_id

    @property
    def pid(self) -> int | None:
        process = self._process
        if process is None or process.poll() is not None:
            return None
        return process.pid

    def metrics_snapshot(self) -> ParserRuntimeMetrics:
        return ParserRuntimeMetrics(
            processes_created=self._processes_created,
            distinct_pids=len(self._pids),
            restarts=self._restarts,
            timeouts=self._timeouts,
            spawn_ms=self._spawn_ms,
            request_ms=self._request_ms,
            parser_cache_hits=self._parser_cache_hits,
            worker_count=1,
        )

    def request(
        self,
        operation: str,
        payload: dict[str, object] | None,
        path: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._request_locked(operation, payload, path)

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is not None:
                self._cleanup_dead_process()
                return
            request_id = self._next_request_id()
            envelope = build_request(
                request_id=request_id,
                operation=OPERATION_SHUTDOWN,
                payload={},
            )
            try:
                self._write_request(envelope)
                with contextlib.suppress(ParserCrashError, ParserTimeoutError):
                    self._wait_response(request_id, path="<shutdown>", timeout=1.0)
            except (OSError, ParserCrashError):
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=_SHUTDOWN_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    _stop_process(process)
            self._cleanup_dead_process()

    def _request_locked(
        self,
        operation: str,
        payload: dict[str, object] | None,
        path: str,
    ) -> dict[str, Any]:
        self._ensure_process(path)
        request_id = self._next_request_id()
        envelope = build_request(
            request_id=request_id,
            operation=operation,
            payload=payload,
        )
        request_started = time.perf_counter()
        try:
            self._write_request(envelope)
            response = self._wait_response(
                request_id,
                path=path,
                timeout=self._timeout_seconds,
            )
        except ParserTimeoutError:
            self._timeouts += 1
            self._kill_and_reset(path, reason="timeout")
            raise
        except ParserCrashError:
            self._kill_and_reset(path, reason="crash")
            raise
        finally:
            self._request_ms += max(0, round((time.perf_counter() - request_started) * 1000))

        validated = validate_response(response, expected_request_id=request_id)
        if not validated["ok"]:
            error = validated["error"]
            message = f"{error.get('type', 'Error')}: {error.get('message', 'unknown')}"
            if str(error.get("kind")) == ERROR_KIND_ANALYSIS:
                # Valid protocol response - keep the worker alive; do not open circuit.
                raise ParserCrashError(path, message, opens_circuit=False)
            self._kill_and_reset(path, reason="protocol")
            raise ParserCrashError(path, message)

        result = validated["result"]
        assert isinstance(result, dict)
        if result.get("parser_cache_hit") is True:
            self._parser_cache_hits += 1
        elif isinstance(result.get("parser_cache_hits"), int):
            # Health responses expose cumulative worker totals; prefer per-request flag.
            pass
        return result

    def _ensure_process(self, path: str) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            return
        if process is not None:
            self._cleanup_dead_process()
        self._start_process(path)

    def _start_process(self, path: str) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        spawn_started = time.perf_counter()
        try:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as error:
            raise ParserCrashError(path, f"Could not start parser worker: {error}") from error
        spawn_ms = max(0, round((time.perf_counter() - spawn_started) * 1000))
        self._spawn_ms += spawn_ms
        self._processes_created += 1
        if self._ever_started:
            self._restarts += 1
        self._ever_started = True
        if process.pid is not None:
            self._pids.add(process.pid)
        self._process = process
        self._response_queue = Queue()
        self._stderr_lines = deque()
        self._stderr_bytes = 0
        self._stdout_thread = threading.Thread(
            target=self._stdout_reader,
            name=f"{self._slot_id}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader,
            name=f"{self._slot_id}-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _write_request(self, envelope: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ParserCrashError("<worker>", "Parser worker stdin is unavailable.")
        try:
            process.stdin.write(json.dumps(envelope, ensure_ascii=True) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            snippet = self._stderr_snippet()
            detail = f"Parser worker pipe broke while writing: {error}"
            if snippet:
                detail = f"{detail}\n{snippet}"
            raise ParserCrashError("<worker>", detail) from error

    def _wait_response(
        self,
        request_id: str,
        *,
        path: str,
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ParserTimeoutError(path, timeout)
            try:
                kind, value = self._response_queue.get(timeout=remaining)
            except Empty as error:
                raise ParserTimeoutError(path, timeout) from error
            if kind == "line":
                line = str(value)
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as error:
                    snippet = self._stderr_snippet()
                    detail = "Parser worker returned invalid JSON."
                    if snippet:
                        detail = f"{detail}\n{snippet}"
                    raise ParserCrashError(path, detail) from error
                try:
                    return validate_response(decoded, expected_request_id=request_id)
                except ValueError as error:
                    snippet = self._stderr_snippet()
                    detail = f"Parser worker returned an invalid response: {error}"
                    if snippet:
                        detail = f"{detail}\n{snippet}"
                    raise ParserCrashError(path, detail) from error
            if kind == "eof":
                snippet = self._stderr_snippet()
                detail = "Parser worker exited before answering."
                if snippet:
                    detail = f"{detail}\n{snippet}"
                raise ParserCrashError(path, detail)
            if kind == "error":
                snippet = self._stderr_snippet()
                detail = f"Parser worker stdout reader failed: {value}"
                if snippet:
                    detail = f"{detail}\n{snippet}"
                raise ParserCrashError(path, detail)
            raise ParserCrashError(path, f"Unexpected reader event: {kind}")

    def _stdout_reader(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            self._response_queue.put(("eof", None))
            return
        try:
            for line in stream:
                self._response_queue.put(("line", line))
        except Exception as error:  # reader must never kill the parent
            self._response_queue.put(("error", error))
        finally:
            self._response_queue.put(("eof", None))

    def _stderr_reader(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        try:
            for line in stream:
                self._append_stderr(line.rstrip("\r\n"))
        except Exception:
            return

    def _append_stderr(self, line: str) -> None:
        encoded = len(line.encode("utf-8", errors="replace"))
        self._stderr_lines.append(line)
        self._stderr_bytes += encoded
        while self._stderr_lines and (
            len(self._stderr_lines) > _STDERR_MAX_LINES or self._stderr_bytes > _STDERR_MAX_BYTES
        ):
            removed = self._stderr_lines.popleft()
            self._stderr_bytes -= len(removed.encode("utf-8", errors="replace"))
            if self._stderr_bytes < 0:
                self._stderr_bytes = 0

    def _stderr_snippet(self) -> str:
        if not self._stderr_lines:
            return ""
        return "stderr:\n" + "\n".join(self._stderr_lines)

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"{self._slot_id}-{self._request_counter:06d}"

    def _kill_and_reset(self, path: str, *, reason: str) -> None:
        del path, reason
        process = self._process
        if process is not None and process.poll() is None:
            _stop_process(process)
        self._cleanup_dead_process()

    def _cleanup_dead_process(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread
        self._stdout_thread = None
        self._stderr_thread = None
        if stdout_thread is not None and stdout_thread.is_alive():
            stdout_thread.join(timeout=_READER_JOIN_SECONDS)
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=_READER_JOIN_SECONDS)
        while True:
            try:
                self._response_queue.get_nowait()
            except Empty:
                break
        self._stderr_lines = deque()
        self._stderr_bytes = 0


class NativeParserSupervisor:
    """Runs parser requests through a supervised pool of persistent child processes."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60.0,
        circuit_reset_seconds: float = 60.0,
        worker_count: int = 1,
        command: Sequence[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= worker_count <= 8:
            raise ValueError("worker_count must be between 1 and 8")
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._failure_threshold = failure_threshold
        self._failure_window_seconds = failure_window_seconds
        self._circuit_reset_seconds = circuit_reset_seconds
        self._worker_count = worker_count
        self._command = tuple(
            command
            or (
                sys.executable,
                "-u",
                "-m",
                "code_harness.infrastructure.parsers.native_worker",
                "--loop",
            )
        )
        self._clock = clock
        self._state_lock = threading.Lock()
        self._circuits: dict[str, _CircuitState] = {}
        self._problem_payloads: set[ProblemPayloadKey] = set()
        self._in_flight = 0
        self._max_concurrent_analyses = 0
        self._shutdown = False
        self._slots: tuple[_PersistentWorkerSlot, ...] = tuple(
            _PersistentWorkerSlot(
                self._command,
                timeout_seconds=timeout_seconds,
                slot_id=f"worker-{index}",
            )
            for index in range(worker_count)
        )
        self._available_slots: Queue[_PersistentWorkerSlot] = Queue()
        for slot in self._slots:
            self._available_slots.put(slot)

    @property
    def name(self) -> str:
        return "native-parser-supervisor"

    @property
    def version(self) -> str:
        # ANALYSIS_VERSION only - transport changes must not bump this.
        return ANALYSIS_VERSION

    @property
    def protocol_version(self) -> int:
        return PROTOCOL_VERSION

    @property
    def worker_implementation_version(self) -> str:
        return WORKER_IMPLEMENTATION_VERSION

    @property
    def worker_count(self) -> int:
        return self._worker_count

    def supports(self, language: str) -> bool:
        return self._enabled and language.casefold() in {"java", "python", "plsql"}

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        language = request.language.casefold()
        if not self.supports(language):
            raise ParserUnavailableError(request.language)
        key: ProblemPayloadKey = (
            language,
            OPERATION_ANALYZE,
            request.path,
            request.content_hash,
            ANALYSIS_VERSION,
        )
        with self._state_lock:
            if self._shutdown:
                raise ParserCrashError(request.path, "Parser supervisor has been shut down.")
            if key in self._problem_payloads:
                raise ParserCrashError(
                    request.path,
                    "Parser skipped a previously failing payload.",
                )
            self._check_circuit_locked(language)
        slot = self._acquire_slot()
        try:
            self._begin_analysis()
            try:
                payload = slot.request(
                    OPERATION_ANALYZE,
                    {
                        "request_id": request.request_id,
                        "path": request.path,
                        "language": language,
                        "content": request.content,
                        "content_hash": request.content_hash,
                    },
                    request.path,
                )
                result = _decode_result(payload)
            except ParserTimeoutError:
                with self._state_lock:
                    self._problem_payloads.add(key)
                    self._record_failure_locked(key)
                raise
            except ParserCrashError as error:
                if error.details.get("opens_circuit", True):
                    with self._state_lock:
                        self._problem_payloads.add(key)
                        self._record_failure_locked(key)
                raise
            finally:
                self._end_analysis()
            return result
        finally:
            self._release_slot(slot)

    def health_check(self) -> bool:
        if not self._enabled:
            return True
        with self._state_lock:
            if self._shutdown:
                return False
        slot = self._acquire_slot()
        try:
            response = slot.request(OPERATION_HEALTH, {}, "<health-check>")
        except (ParserCrashError, ParserTimeoutError):
            return False
        finally:
            self._release_slot(slot)
        return response.get("status") == "ok"

    def shutdown(self) -> None:
        with self._state_lock:
            self._shutdown = True
        for slot in self._slots:
            slot.shutdown()

    def metrics_snapshot(self) -> ParserRuntimeMetrics:
        processes_created = 0
        pids: set[int] = set()
        restarts = 0
        timeouts = 0
        spawn_ms = 0
        request_ms = 0
        parser_cache_hits = 0
        for slot in self._slots:
            metrics = slot.metrics_snapshot()
            processes_created += metrics.processes_created
            restarts += metrics.restarts
            timeouts += metrics.timeouts
            spawn_ms += metrics.spawn_ms
            request_ms += metrics.request_ms
            parser_cache_hits += metrics.parser_cache_hits
            pids.update(slot._pids)
        with self._state_lock:
            max_concurrent = self._max_concurrent_analyses
        return ParserRuntimeMetrics(
            processes_created=processes_created,
            distinct_pids=len(pids),
            restarts=restarts,
            timeouts=timeouts,
            spawn_ms=spawn_ms,
            request_ms=request_ms,
            parser_cache_hits=parser_cache_hits,
            worker_count=self._worker_count,
            max_concurrent_analyses=max_concurrent,
        )

    def _acquire_slot(self) -> _PersistentWorkerSlot:
        return self._available_slots.get()

    def _release_slot(self, slot: _PersistentWorkerSlot) -> None:
        self._available_slots.put(slot)

    def _begin_analysis(self) -> None:
        with self._state_lock:
            self._in_flight += 1
            if self._in_flight > self._max_concurrent_analyses:
                self._max_concurrent_analyses = self._in_flight

    def _end_analysis(self) -> None:
        with self._state_lock:
            self._in_flight = max(0, self._in_flight - 1)

    def _check_circuit_locked(self, language: str) -> None:
        state = self._circuits.get(language)
        if state is None or state.opened_at is None:
            return
        if self._clock() - state.opened_at >= self._circuit_reset_seconds:
            self._circuits.pop(language, None)
            return
        raise ParserCircuitOpenError(language)

    def _record_failure_locked(self, key: ProblemPayloadKey) -> None:
        language = key[0]
        now = self._clock()
        state = self._circuits.setdefault(language, _CircuitState())
        while state.recent_failures and (
            now - state.recent_failures[0][0] > self._failure_window_seconds
        ):
            state.recent_failures.popleft()
        if all(existing != key for _, existing in state.recent_failures):
            state.recent_failures.append((now, key))
        distinct = {existing for _, existing in state.recent_failures}
        if len(distinct) >= self._failure_threshold:
            state.opened_at = now


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
    required = ("parser_name", "parser_version", "state")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ParserCrashError(
            "<worker>",
            f"Parser result missing required fields: {', '.join(missing)}.",
        )
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
            canonical_signature=(
                str(item["canonical_signature"]) if item.get("canonical_signature") else None
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
