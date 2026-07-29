from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from code_harness.domain.errors import ExecutionNotSupportedError, ProcessStartError

_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER_STRUCT(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def create_private_directory(path: Path) -> None:
    """Create a Windows directory with a protected current-user/System DACL."""

    if os.name != "nt":
        raise ExecutionNotSupportedError("PowerShell execution currently requires Windows.")
    parent = path.parent.resolve(strict=True)
    candidate = path.resolve(strict=False)
    if candidate.parent != parent:
        raise ProcessStartError("PowerShell artifact directory escaped its configured parent.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    sid = _current_user_sid(kernel32, advapi32)
    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})"
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    )
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        None,
    ):
        raise ProcessStartError(
            "Could not build the PowerShell artifact security descriptor.",
            winerror=ctypes.get_last_error(),
        )
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES),
        descriptor,
        False,
    )
    kernel32.CreateDirectoryW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    )
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    try:
        if not kernel32.CreateDirectoryW(str(candidate), ctypes.byref(attributes)):
            raise ProcessStartError(
                "Could not create the protected PowerShell artifact directory.",
                winerror=ctypes.get_last_error(),
            )
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)


def _current_user_sid(kernel32: ctypes.WinDLL, advapi32: ctypes.WinDLL) -> str:
    token = wintypes.HANDLE()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_uint,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ProcessStartError(
            "Could not open the current process token.",
            winerror=ctypes.get_last_error(),
        )
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise ProcessStartError(
                "Could not size the current user token.",
                winerror=ctypes.get_last_error(),
            )
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_USER,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise ProcessStartError(
                "Could not read the current user token.",
                winerror=ctypes.get_last_error(),
            )
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER_STRUCT)).contents
        string_sid = wintypes.LPWSTR()
        advapi32.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi32.ConvertSidToStringSidW(token_user.User.Sid, ctypes.byref(string_sid)):
            raise ProcessStartError(
                "Could not render the current user SID.",
                winerror=ctypes.get_last_error(),
            )
        try:
            rendered_sid = string_sid.value
            if rendered_sid is None:
                raise ProcessStartError("Windows returned an empty current user SID.")
            return rendered_sid
        finally:
            kernel32.LocalFree(string_sid)
    finally:
        kernel32.CloseHandle(token)
