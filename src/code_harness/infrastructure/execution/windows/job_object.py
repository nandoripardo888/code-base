"""Small ctypes wrapper around the Job Object operations used by E1."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from code_harness.domain.errors import ExecutionNotSupportedError, ProcessStartError

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObject:
    def __init__(self, *, max_processes: int) -> None:
        if ctypes.sizeof(ctypes.c_void_p) == 0:  # pragma: no cover - defensive
            raise ExecutionNotSupportedError("Windows Job Objects are unavailable.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ProcessStartError(
                "Could not create the Windows Job Object.", winerror=ctypes.get_last_error()
            )
        self._kernel32 = kernel32
        self._handle: int | None = int(handle)
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes
        if not kernel32.SetInformationJobObject(
            wintypes.HANDLE(self._handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ProcessStartError("Could not configure the Windows Job Object.", winerror=error)

    def assign(self, process_handle: int) -> None:
        if self._handle is None or not self._kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(self._handle), wintypes.HANDLE(process_handle)
        ):
            raise ProcessStartError(
                "Could not assign process to the Windows Job Object.",
                winerror=ctypes.get_last_error(),
            )

    def terminate(self) -> None:
        if self._handle is not None:
            self._kernel32.TerminateJobObject(wintypes.HANDLE(self._handle), 1)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None

    def __enter__(self) -> JobObject:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
