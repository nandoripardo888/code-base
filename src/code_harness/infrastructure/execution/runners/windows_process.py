"""Create a suspended child, attach it to a Job Object, then collect bounded output."""

from __future__ import annotations

import ctypes
import hashlib
import msvcrt
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from code_harness.domain.errors import ProcessStartError
from code_harness.domain.models.execution import ProcessRunOutcome
from code_harness.domain.protocols.execution_runtime import ExecutionTaskControl
from code_harness.infrastructure.execution.windows.job_object import JobObject

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _OutputCollector:
    def __init__(self, *, total_limit: int) -> None:
        self._limit = total_limit
        self._lock = threading.Lock()
        self._retained = 0
        self._items: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        self._seen = {"stdout": 0, "stderr": 0}
        self._truncated = {"stdout": False, "stderr": False}
        self._hashes = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}

    def drain(self, name: str, handle: int) -> None:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        try:
            while chunk := os.read(fd, 65_536):
                with self._lock:
                    self._seen[name] += len(chunk)
                    self._hashes[name].update(chunk)
                    remaining = self._limit - self._retained
                    if remaining > 0:
                        kept = chunk[:remaining]
                        self._items[name].extend(kept)
                        self._retained += len(kept)
                    if len(chunk) > remaining:
                        self._truncated[name] = True
        finally:
            os.close(fd)

    def result(
        self,
        *,
        exit_code: int | None,
        timed_out: bool,
        cancelled: bool,
        elapsed_ms: int,
    ) -> ProcessRunOutcome:
        return ProcessRunOutcome(
            exit_code=exit_code,
            stdout=self._items["stdout"].decode("utf-8", errors="replace"),
            stderr=self._items["stderr"].decode("utf-8", errors="replace"),
            stdout_bytes=self._seen["stdout"],
            stderr_bytes=self._seen["stderr"],
            stdout_truncated=self._truncated["stdout"],
            stderr_truncated=self._truncated["stderr"],
            timed_out=timed_out,
            cancelled=cancelled,
            elapsed_ms=elapsed_ms,
            stdout_sha256=self._hashes["stdout"].hexdigest(),
            stderr_sha256=self._hashes["stderr"].hexdigest(),
        )


def _safe_environment(executable: str, temp_dir: Path) -> dict[str, str]:
    keep = ("SystemRoot", "WINDIR", "PATHEXT", "ComSpec", "USERPROFILE", "HOME")
    environment = {key: os.environ[key] for key in keep if os.environ.get(key)}
    system_root = environment.get("SystemRoot") or environment.get("WINDIR", r"C:\\Windows")
    environment["PATH"] = os.pathsep.join(
        (str(Path(executable).parent), str(Path(system_root) / "System32"), system_root)
    )
    environment["TEMP"] = str(temp_dir)
    environment["TMP"] = str(temp_dir)
    return environment


def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    return ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0"
    )


def _pipe(kernel32: ctypes.WinDLL) -> tuple[int, int]:
    read = wintypes.HANDLE()
    write = wintypes.HANDLE()
    security = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    if not kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(security), 0):
        raise ProcessStartError(
            "Could not create an output pipe.", winerror=ctypes.get_last_error()
        )
    if not kernel32.SetHandleInformation(read, _HANDLE_FLAG_INHERIT, 0):
        kernel32.CloseHandle(read)
        kernel32.CloseHandle(write)
        raise ProcessStartError(
            "Could not secure an output pipe.", winerror=ctypes.get_last_error()
        )
    if read.value is None or write.value is None:  # pragma: no cover - WinAPI contract
        raise ProcessStartError("Windows returned an invalid output pipe handle.")
    return int(read.value), int(write.value)


def run_windows_process(
    *,
    executable: str,
    args: tuple[str, ...],
    cwd: str,
    timeout_seconds: float,
    max_output_bytes: int,
    execution_home: Path,
    max_processes: int,
    control: ExecutionTaskControl | None = None,
    on_started: Callable[[], None] | None = None,
) -> ProcessRunOutcome:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    )
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    for name in (
        "CreatePipe",
        "SetHandleInformation",
        "CloseHandle",
        "ResumeThread",
        "WaitForSingleObject",
        "GetExitCodeProcess",
    ):
        getattr(kernel32, name).restype = (
            wintypes.BOOL if name not in {"WaitForSingleObject", "ResumeThread"} else wintypes.DWORD
        )
    execution_home.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if control is not None and control.cancellation_requested:
        return ProcessRunOutcome(
            exit_code=None,
            stdout="",
            stderr="",
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            cancelled=True,
            elapsed_ms=0,
        )
    with (
        tempfile.TemporaryDirectory(prefix="e1-", dir=execution_home) as temp_name,
        JobObject(max_processes=max_processes) as job,
    ):
        stdout_read, stdout_write = _pipe(kernel32)
        stderr_read, stderr_write = _pipe(kernel32)
        nul = kernel32.CreateFileW("NUL", 0x80000000, 0x00000001 | 0x00000002, None, 3, 0, None)
        if nul == wintypes.HANDLE(-1).value:
            raise ProcessStartError(
                "Could not open NUL for child stdin.", winerror=ctypes.get_last_error()
            )
        process = _PROCESS_INFORMATION()
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = _STARTF_USESTDHANDLES
        startup.hStdInput = nul
        startup.hStdOutput = stdout_write
        startup.hStdError = stderr_write
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline((executable, *args)))
        env = _environment_block(_safe_environment(executable, Path(temp_name)))
        assigned = False
        try:
            if not kernel32.CreateProcessW(
                executable,
                command_line,
                None,
                None,
                True,
                _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT,
                env,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(process),
            ):
                raise ProcessStartError(
                    "Could not start the requested process.",
                    executable=executable,
                    winerror=ctypes.get_last_error(),
                )
            job.assign(int(process.hProcess))
            assigned = True
            if control is not None:
                control.register_terminator(job.terminate)
            kernel32.CloseHandle(stdout_write)
            stdout_write = 0
            kernel32.CloseHandle(stderr_write)
            stderr_write = 0
            kernel32.CloseHandle(nul)
            nul = 0
            collector = _OutputCollector(total_limit=max_output_bytes)
            readers = [
                threading.Thread(target=collector.drain, args=("stdout", stdout_read), daemon=True),
                threading.Thread(target=collector.drain, args=("stderr", stderr_read), daemon=True),
            ]
            stdout_read = 0
            stderr_read = 0
            for reader in readers:
                reader.start()
            cancelled_before_resume = control is not None and control.cancellation_requested
            if not cancelled_before_resume:
                kernel32.ResumeThread(process.hThread)
                if on_started is not None:
                    on_started()
            wait_ms = max(1, int(timeout_seconds * 1000))
            timed_out = kernel32.WaitForSingleObject(process.hProcess, wait_ms) == _WAIT_TIMEOUT
            cancelled = control is not None and control.cancellation_requested
            if timed_out and not cancelled:
                job.terminate()
                kernel32.WaitForSingleObject(process.hProcess, 5_000)
            elif cancelled:
                kernel32.WaitForSingleObject(process.hProcess, 5_000)
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
            for reader in readers:
                reader.join(timeout=5)
            return collector.result(
                exit_code=None if timed_out or cancelled else int(code.value),
                timed_out=timed_out and not cancelled,
                cancelled=cancelled,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            if control is not None:
                control.clear_terminator()
            if process.hProcess and not assigned:
                kernel32.TerminateProcess(process.hProcess, 1)
            for handle in (
                stdout_read,
                stderr_read,
                stdout_write,
                stderr_write,
                nul,
                int(process.hThread or 0),
                int(process.hProcess or 0),
            ):
                if handle:
                    kernel32.CloseHandle(wintypes.HANDLE(handle))
