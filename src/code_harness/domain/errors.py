from collections.abc import Mapping
from typing import Any

from code_harness.domain.enums import ErrorCode

_RECOVERABLE_CODES = frozenset(
    {
        ErrorCode.RIPGREP_UNAVAILABLE,
        ErrorCode.RIPGREP_TIMEOUT,
        ErrorCode.EMBEDDING_UNAVAILABLE,
        ErrorCode.PARSER_UNAVAILABLE,
        ErrorCode.PARSER_TIMEOUT,
        ErrorCode.PARSER_CIRCUIT_OPEN,
        ErrorCode.INDEX_NOT_READY,
    }
)

_ERROR_CAPABILITIES: dict[ErrorCode, str] = {
    ErrorCode.RIPGREP_UNAVAILABLE: "ripgrep",
    ErrorCode.RIPGREP_TIMEOUT: "ripgrep",
    ErrorCode.EMBEDDING_UNAVAILABLE: "semantic",
    ErrorCode.PARSER_UNAVAILABLE: "structural",
    ErrorCode.PARSER_TIMEOUT: "structural",
    ErrorCode.PARSER_CRASH: "structural",
    ErrorCode.PARSER_CIRCUIT_OPEN: "structural",
    ErrorCode.INDEX_NOT_READY: "catalog",
    ErrorCode.INDEX_CORRUPTED: "catalog",
}

_DEFAULT_REMEDIATIONS: dict[ErrorCode, str] = {
    ErrorCode.RIPGREP_UNAVAILABLE: (
        "Install Ripgrep or configure CODE_HARNESS_RG with the full path to rg."
    ),
    ErrorCode.RIPGREP_TIMEOUT: "Retry with a narrower query or increase the timeout.",
    ErrorCode.EMBEDDING_UNAVAILABLE: (
        "Install compatible semantic dependencies or disable semantic search."
    ),
    ErrorCode.PARSER_UNAVAILABLE: "Install parser support for the language or reindex.",
    ErrorCode.PARSER_TIMEOUT: "Retry indexing or increase the parser timeout.",
    ErrorCode.PARSER_CIRCUIT_OPEN: "Wait for the parser circuit to close or reindex.",
    ErrorCode.INDEX_NOT_READY: "Run index_project before structural or indexed searches.",
}


class CodeHarnessError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        recoverable: bool | None = None,
        capability: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.recoverable = (
            bool(recoverable) if recoverable is not None else code in _RECOVERABLE_CODES
        )
        self.capability = capability if capability is not None else _ERROR_CAPABILITIES.get(code)
        self.remediation = (
            remediation if remediation is not None else _DEFAULT_REMEDIATIONS.get(code)
        )


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
            remediation="Run code-harness doctor and configure CODE_HARNESS_RG.",
        )


class RipgrepTimeoutError(CodeHarnessError):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            ErrorCode.RIPGREP_TIMEOUT,
            f"Ripgrep timed out after {timeout_seconds} seconds.",
            details={"timeout_seconds": timeout_seconds},
        )


class IndexNotReadyError(CodeHarnessError):
    def __init__(self, message: str = "The project index is not ready.") -> None:
        super().__init__(ErrorCode.INDEX_NOT_READY, message)


class IndexCorruptedError(CodeHarnessError):
    def __init__(self, message: str, *, path: str | None = None) -> None:
        details = {"path": path} if path is not None else None
        super().__init__(ErrorCode.INDEX_CORRUPTED, message, details=details)


class ParserUnavailableError(CodeHarnessError):
    def __init__(self, language: str) -> None:
        super().__init__(
            ErrorCode.PARSER_UNAVAILABLE,
            f"No structural parser is available for language: {language}",
            details={"language": language},
        )


class ParserTimeoutError(CodeHarnessError):
    def __init__(self, path: str, timeout_seconds: float) -> None:
        super().__init__(
            ErrorCode.PARSER_TIMEOUT,
            f"Structural parser timed out for {path}.",
            details={"path": path, "timeout_seconds": timeout_seconds},
        )


class ParserCrashError(CodeHarnessError):
    def __init__(self, path: str, message: str = "Structural parser worker failed.") -> None:
        super().__init__(
            ErrorCode.PARSER_CRASH,
            message,
            details={"path": path},
        )


class ParserCircuitOpenError(CodeHarnessError):
    def __init__(self, language: str) -> None:
        super().__init__(
            ErrorCode.PARSER_CIRCUIT_OPEN,
            f"Structural parser circuit is open for language: {language}",
            details={"language": language},
        )


class EmbeddingUnavailableError(CodeHarnessError):
    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(ErrorCode.EMBEDDING_UNAVAILABLE, message, remediation=remediation)


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


class CursorStaleError(CodeHarnessError):
    def __init__(self, message: str = "Pagination cursor is stale for the current index.") -> None:
        super().__init__(ErrorCode.CURSOR_STALE, message)


def is_recoverable_error(error: BaseException) -> bool:
    return isinstance(error, CodeHarnessError) and error.recoverable
