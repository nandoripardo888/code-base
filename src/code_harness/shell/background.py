"""Concurrent registry and bounded output access for shell jobs."""

from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import hmac
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from code_harness.errors import ExecutionError, InvalidArgumentError

_TERMINATE_GRACE_SECONDS = 3.0
TAIL_MAX_BYTES = 64 * 1024
DEFAULT_MAX_RUNNING = 8
DEFAULT_MAX_RETAINED = 100
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
OUTPUT_CURSOR_START = "start"
_OUTPUT_CURSOR_PREFIX = "v1."
_MAX_CURSOR_LENGTH = 512


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer.") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True, slots=True)
class JobPolicy:
    max_running: int = DEFAULT_MAX_RUNNING
    max_retained: int = DEFAULT_MAX_RETAINED
    retention_seconds: int = DEFAULT_RETENTION_SECONDS

    def __post_init__(self) -> None:
        for name, value in (
            ("max_running", self.max_running),
            ("max_retained", self.max_retained),
            ("retention_seconds", self.retention_seconds),
        ):
            if value < 1:
                raise ValueError(f"{name} must be a positive integer.")

    @classmethod
    def from_environment(cls) -> JobPolicy:
        return cls(
            max_running=_env_positive_int(
                "CODE_HARNESS_JOBS_MAX_RUNNING", DEFAULT_MAX_RUNNING
            ),
            max_retained=_env_positive_int(
                "CODE_HARNESS_JOBS_MAX_RETAINED", DEFAULT_MAX_RETAINED
            ),
            retention_seconds=_env_positive_int(
                "CODE_HARNESS_JOBS_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS
            ),
        )


@dataclass(slots=True)
class ShellJob:
    job_id: str
    process: subprocess.Popen[bytes]
    command: str
    working_directory: Path
    shell_name: str
    shell_executable: str
    output_path: Path
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    exit_code: int | None = None
    cancelled: bool = False
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def status(self) -> str:
        self.refresh()
        with self._state_lock:
            if self.cancelled:
                return "cancelled"
            if self.finished_at is None:
                return "running"
            return "completed" if self.exit_code == 0 else "failed"

    @property
    def elapsed_ms(self) -> int:
        self.refresh()
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0, int((end - self.started_at) * 1000))

    def wait(self, timeout_seconds: float) -> bool:
        """Wait for completion, returning False when the timeout elapses."""
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return False
        self.refresh()
        return True

    def refresh(self) -> None:
        return_code = self.process.poll()
        if return_code is None:
            return
        with self._state_lock:
            if self.finished_at is None:
                self.exit_code = return_code
                self.finished_at = time.monotonic()

    def terminate(self) -> None:
        if self.process.poll() is None:
            self._terminate_process_tree()
            try:
                self.process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill_process_tree()
                self.process.wait()
        self.refresh()

    def cancel(self) -> bool:
        """Cancel a running process tree; return whether this call initiated it."""

        with self._cancel_lock:
            self.refresh()
            with self._state_lock:
                if self.finished_at is not None or self.cancelled:
                    return False
                return_code = self.process.poll()
                if return_code is not None:
                    self.exit_code = return_code
                    self.finished_at = time.monotonic()
                    return False
                self.cancelled = True
            self.terminate()
            return True

    def _terminate_process_tree(self) -> None:
        try:
            if os.name == "nt":
                _terminate_windows_process_tree(self.process)
            else:
                killpg = os.__dict__["killpg"]
                getpgid = os.__dict__["getpgid"]
                killpg(getpgid(self.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            self.process.terminate()

    def _kill_process_tree(self) -> None:
        try:
            if os.name == "nt":
                _terminate_windows_process_tree(self.process)
            else:
                killpg = os.__dict__["killpg"]
                getpgid = os.__dict__["getpgid"]
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                killpg(getpgid(self.pid), sigkill)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            self.process.kill()


def _terminate_windows_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate a Windows process tree, with a native fallback for orphaned children."""

    descendants = _windows_descendant_pids(process.pid)
    # Open and terminate descendants before the root so handles cannot be confused
    # with a rapidly reused PID after taskkill returns.
    _terminate_windows_pids(reversed(descendants))
    with suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_TERMINATE_GRACE_SECONDS,
        )
    if process.poll() is None:
        process.terminate()


def _windows_descendant_pids(root_pid: int) -> list[int]:
    """Snapshot descendant PIDs without requiring WMI or administrator access."""

    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return []
    parent_by_pid: dict[int, int] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)

    descendants: list[int] = []
    frontier = [root_pid]
    seen = {root_pid}
    while frontier:
        parents = set(frontier)
        frontier = [
            pid
            for pid, parent in parent_by_pid.items()
            if parent in parents and pid not in seen
        ]
        descendants.extend(frontier)
        seen.update(frontier)
    return descendants


def _terminate_windows_pids(pids: Iterable[int]) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    for pid in pids:
        # PROCESS_TERMINATE | SYNCHRONIZE: termination plus a bounded wait for
        # inherited log handles to close before registry cleanup.
        handle = kernel32.OpenProcess(0x00100001, False, pid)
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(handle, 1):
                kernel32.WaitForSingleObject(handle, int(_TERMINATE_GRACE_SECONDS * 1000))
        finally:
            kernel32.CloseHandle(handle)


@dataclass(frozen=True, slots=True)
class TailResult:
    output: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class OutputChunk:
    output: str
    next_offset: int
    has_more: bool


@dataclass(slots=True)
class LaunchReservation:
    """One concurrency slot reserved before a process or log file is created."""

    registry: JobRegistry
    output_path: Path
    _active: bool = True

    def __enter__(self) -> LaunchReservation:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def register(self, job: ShellJob) -> str:
        return self.registry._register_reserved(self, job)

    def release(self) -> None:
        self.registry._release_reservation(self)


def tail_output(path: Path, max_lines: int, max_bytes: int = TAIL_MAX_BYTES) -> TailResult:
    """Read a bounded tail without loading a potentially large log into memory."""
    if max_lines < 1 or max_bytes < 1:
        return TailResult("", False)
    try:
        size = path.stat().st_size
        bytes_to_read = min(size, max_bytes)
        with path.open("rb") as stream:
            stream.seek(max(0, size - bytes_to_read))
            raw = stream.read(bytes_to_read)
    except OSError:
        return TailResult("", False)

    byte_truncated = size > max_bytes
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    line_truncated = len(lines) > max_lines
    if line_truncated:
        lines = lines[-max_lines:]
    return TailResult("".join(lines), byte_truncated or line_truncated)


def read_output_chunk(
    path: Path,
    offset: int,
    *,
    final: bool,
    max_bytes: int = TAIL_MAX_BYTES,
) -> OutputChunk:
    """Read new log bytes while retaining an incomplete UTF-8 suffix for the next poll."""

    if offset < 0 or max_bytes < 1:
        raise ValueError("Invalid output range.")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if offset > size:
            raise ValueError("Output position is no longer available.")
        stream.seek(offset)
        raw = stream.read(max_bytes)
        size_after = os.fstat(stream.fileno()).st_size

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    at_current_end = offset + len(raw) >= size_after
    output = decoder.decode(raw, final=final and at_current_end)
    pending, _state = decoder.getstate()
    consumed = len(raw) - len(pending)
    next_offset = offset + consumed
    return OutputChunk(
        output=output,
        next_offset=next_offset,
        has_more=bool(pending) or next_offset < size_after,
    )


class JobRegistry:
    """Owns the scratch directory and every process launched from it."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        policy: JobPolicy | None = None,
    ) -> None:
        self.policy = policy or JobPolicy.from_environment()
        base = directory or Path(tempfile.mkdtemp(prefix="code-harness-shell-"))
        base.mkdir(parents=True, exist_ok=True)
        # Resolve so the reported paths match what PathGuard accepts, which
        # matters on Windows where the temp dir may come back in 8.3 form.
        self._directory = base.resolve(strict=False)
        self._jobs: dict[str, ShellJob] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._launch_reservations = 0
        self._cursor_secret = secrets.token_bytes(32)

    @property
    def directory(self) -> Path:
        return self._directory

    def prepare_output_path(self) -> Path:
        """Return a fresh log path, recreating the scratch directory if needed."""
        with self._lock:
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            self._prune_locked()
            # The scratch dir can disappear while the server is idle (temp
            # cleaners, external deletion). Recreate it before every launch.
            self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory / f"{uuid.uuid4().hex}.log"

    def reserve_launch(self) -> LaunchReservation:
        """Reserve capacity atomically before creating a process or its log."""

        with self._lock:
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            self._prune_locked()
            running = sum(job.finished_at is None for job in self._jobs.values())
            if running + self._launch_reservations >= self.policy.max_running:
                raise ExecutionError(
                    "Concurrent shell job limit reached "
                    f"({self.policy.max_running} per project)."
                )
            self._directory.mkdir(parents=True, exist_ok=True)
            self._launch_reservations += 1
            return LaunchReservation(
                registry=self,
                output_path=self._directory / f"{uuid.uuid4().hex}.log",
            )

    def register(self, job: ShellJob) -> str:
        with self._lock:
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            self._prune_locked()
            running = sum(existing.finished_at is None for existing in self._jobs.values())
            if running >= self.policy.max_running:
                raise ExecutionError(
                    "Concurrent shell job limit reached "
                    f"({self.policy.max_running} per project)."
                )
            job_id = self._add_job_locked(job)

        self._start_watcher(job)
        return job_id

    def has_running_jobs(self) -> bool:
        """Return whether this registry still owns at least one running process."""

        with self._lock:
            self._prune_locked()
            if self._launch_reservations:
                return True
            jobs = tuple(self._jobs.values())
        return any(job.status == "running" for job in jobs)

    def get(self, job_id: str) -> ShellJob | None:
        with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    def encode_output_cursor(self, job_id: str, offset: int) -> str:
        payload = f"{job_id}\n{offset}".encode("ascii")
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")
        return _OUTPUT_CURSOR_PREFIX + encoded

    def decode_output_cursor(self, cursor: str, job_id: str) -> int:
        if not cursor.startswith(_OUTPUT_CURSOR_PREFIX) or len(cursor) > _MAX_CURSOR_LENGTH:
            raise InvalidArgumentError("cursor is invalid or expired for this job.")
        encoded = cursor[len(_OUTPUT_CURSOR_PREFIX) :]
        try:
            padding = "=" * (-len(encoded) % 4)
            raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
            if len(raw) < 33:
                raise ValueError
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            cursor_job_id, raw_offset = payload.decode("ascii").split("\n", 1)
            offset = int(raw_offset)
            if cursor_job_id != job_id or offset < 0:
                raise ValueError
        except (binascii.Error, UnicodeDecodeError, ValueError) as error:
            raise InvalidArgumentError("cursor is invalid or expired for this job.") from error
        return offset

    def cleanup(self) -> None:
        with self._lock:
            if self._closed:
                jobs: list[ShellJob] = []
            else:
                self._closed = True
                jobs = list(self._jobs.values())
        for job in jobs:
            job.terminate()
        self._remove_directory()

    def _remove_directory(self) -> None:
        # Windows can keep redirected log handles briefly after a forced tree
        # termination even after the root process has reported completion.
        attempts = 60 if os.name == "nt" else 10
        for _ in range(attempts):
            shutil.rmtree(self._directory, ignore_errors=True)
            if not self._directory.exists():
                return
            time.sleep(0.05)

    def _register_reserved(self, reservation: LaunchReservation, job: ShellJob) -> str:
        with self._lock:
            if not reservation._active:
                raise ExecutionError("The shell launch reservation is no longer active.")
            reservation._active = False
            self._launch_reservations -= 1
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            job_id = self._add_job_locked(job)
        self._start_watcher(job)
        return job_id

    def _release_reservation(self, reservation: LaunchReservation) -> None:
        with self._lock:
            if not reservation._active:
                return
            reservation._active = False
            self._launch_reservations -= 1

    def _add_job_locked(self, job: ShellJob) -> str:
        job_id = f"job-{uuid.uuid4()}"
        while job_id in self._jobs:
            job_id = f"job-{uuid.uuid4()}"
        job.job_id = job_id
        self._jobs[job_id] = job
        return job_id

    def _start_watcher(self, job: ShellJob) -> None:
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()

    def _watch(self, job: ShellJob) -> None:
        job.process.wait()
        job.refresh()
        with self._lock:
            self._prune_locked()

    def _prune_locked(self) -> None:
        now = time.monotonic()
        completed: list[tuple[str, ShellJob]] = []
        for job_id, job in self._jobs.items():
            job.refresh()
            if job.finished_at is not None:
                completed.append((job_id, job))

        expired = {
            job_id
            for job_id, job in completed
            if job.finished_at is not None
            and now - job.finished_at >= self.policy.retention_seconds
        }
        retained = [(job_id, job) for job_id, job in completed if job_id not in expired]
        retained.sort(key=lambda item: (item[1].finished_at or 0.0, item[0]))
        excess = max(0, len(retained) - self.policy.max_retained)
        to_remove = expired | {job_id for job_id, _job in retained[:excess]}

        for job_id in to_remove:
            job = self._jobs[job_id]
            try:
                job.output_path.unlink(missing_ok=True)
            except OSError:
                continue
            del self._jobs[job_id]
