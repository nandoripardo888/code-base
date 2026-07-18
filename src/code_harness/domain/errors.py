from collections.abc import Mapping
from typing import Any

from code_harness.domain.enums import ErrorCode


class CodeHarnessError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class ProjectNotFoundError(CodeHarnessError):
    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorCode.PROJECT_NOT_FOUND,
            f"Project directory does not exist: {path}",
            details={"path": path},
        )


class PathOutsideProjectError(CodeHarnessError):
    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorCode.PATH_OUTSIDE_PROJECT,
            "Path resolves outside the project root.",
            details={"path": path},
        )


class SourceFileNotFoundError(CodeHarnessError):
    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorCode.FILE_NOT_FOUND,
            f"Source file does not exist: {path}",
            details={"path": path},
        )


class BinaryFileError(CodeHarnessError):
    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorCode.BINARY_FILE,
            f"File appears to be binary: {path}",
            details={"path": path},
        )


class UnsupportedEncodingError(CodeHarnessError):
    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorCode.UNSUPPORTED_ENCODING,
            f"Could not decode source file: {path}",
            details={"path": path},
        )


class RipgrepUnavailableError(CodeHarnessError):
    def __init__(self, executable: str) -> None:
        super().__init__(
            ErrorCode.RIPGREP_UNAVAILABLE,
            f"Ripgrep executable is unavailable: {executable}",
            details={"executable": executable},
        )


class InvalidQueryError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(ErrorCode.INVALID_QUERY, message, details=details)


class ResultLimitExceededError(CodeHarnessError):
    def __init__(self, path: str, limit: int) -> None:
        super().__init__(
            ErrorCode.RESULT_LIMIT_EXCEEDED,
            f"File exceeds the configured read limit of {limit} bytes: {path}",
            details={"path": path, "limit": limit},
        )
