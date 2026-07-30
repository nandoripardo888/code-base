"""Concurrent registry and bounded output access for shell jobs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from code_harness.errors import ExecutionError

_TERMINATE_GRACE_SECONDS = 3.0
TAIL_MAX_BYTES = 64 * 1024


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
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def status(self) -> str:
        self.refresh()
        if self.process.poll() is None:
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

    def _terminate_process_tree(self) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=_TERMINATE_GRACE_SECONDS,
                )
            else:
                killpg = os.__dict__["killpg"]
                getpgid = os.__dict__["getpgid"]
                killpg(getpgid(self.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            self.process.terminate()

    def _kill_process_tree(self) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=_TERMINATE_GRACE_SECONDS,
                )
            else:
                killpg = os.__dict__["killpg"]
                getpgid = os.__dict__["getpgid"]
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                killpg(getpgid(self.pid), sigkill)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            self.process.kill()


@dataclass(frozen=True, slots=True)
class TailResult:
    output: str
    truncated: bool


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


class JobRegistry:
    """Owns the scratch directory and every process launched from it."""

    def __init__(self, directory: Path | None = None) -> None:
        base = directory or Path(tempfile.mkdtemp(prefix="code-harness-shell-"))
        base.mkdir(parents=True, exist_ok=True)
        # Resolve so the reported paths match what PathGuard accepts, which
        # matters on Windows where the temp dir may come back in 8.3 form.
        self._directory = base.resolve(strict=False)
        self._jobs: dict[str, ShellJob] = {}
        self._lock = threading.Lock()
        self._closed = False

    @property
    def directory(self) -> Path:
        return self._directory

    def prepare_output_path(self) -> Path:
        """Return a fresh log path, recreating the scratch directory if needed."""
        with self._lock:
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            # The scratch dir can disappear while the server is idle (temp
            # cleaners, external deletion). Recreate it before every launch.
            self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory / f"{uuid.uuid4().hex}.log"

    def register(self, job: ShellJob) -> str:
        with self._lock:
            if self._closed:
                raise ExecutionError("The shell job registry is already closed.")
            job_id = f"job-{uuid.uuid4()}"
            while job_id in self._jobs:
                job_id = f"job-{uuid.uuid4()}"
            job.job_id = job_id
            self._jobs[job_id] = job

        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return job_id

    def get(self, job_id: str) -> ShellJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cleanup(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.terminate()
        self._remove_directory()

    def _remove_directory(self) -> None:
        for _ in range(10):
            shutil.rmtree(self._directory, ignore_errors=True)
            if not self._directory.exists():
                return
            time.sleep(0.05)

    @staticmethod
    def _watch(job: ShellJob) -> None:
        job.process.wait()
        job.refresh()
