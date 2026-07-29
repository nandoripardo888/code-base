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


class InternalToolError(CodeHarnessError):
    def __init__(self, tool: str, error_id: str) -> None:
        super().__init__(
            ErrorCode.INTERNAL_ERROR,
            "An unexpected internal error occurred.",
            details={"error_id": error_id, "tool": tool},
            recoverable=False,
            capability=tool,
            remediation="Retry the operation and use error_id to locate the server log.",
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
    def __init__(
        self,
        path: str,
        message: str = "Structural parser worker failed.",
        *,
        opens_circuit: bool = True,
    ) -> None:
        super().__init__(
            ErrorCode.PARSER_CRASH,
            message,
            details={"path": path, "opens_circuit": opens_circuit},
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


class InvalidPathKindError(CodeHarnessError):
    def __init__(self, path: str, *, expected: str, actual: str) -> None:
        super().__init__(
            ErrorCode.INVALID_PATH_KIND,
            f"Path kind mismatch for {path}: expected {expected}, found {actual}.",
            details={"path": path, "expected": expected, "actual": actual},
        )


class ExecutionDisabledError(CodeHarnessError):
    def __init__(
        self,
        message: str = "Command execution is disabled. Set CODE_HARNESS_EXECUTION=1 to enable.",
    ) -> None:
        super().__init__(
            ErrorCode.EXECUTION_DISABLED,
            message,
            capability="execution",
            remediation=(
                "Enable execution with CODE_HARNESS_EXECUTION=1 after reviewing the trust boundary."
            ),
        )


class ExecutionNotSupportedError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.EXECUTION_NOT_SUPPORTED,
            message,
            details=details,
            capability="execution",
        )


class ExecutionPolicyDeniedError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.EXECUTION_POLICY_DENIED,
            message,
            details=details,
            capability="execution",
        )


class ExecutionApprovalRequiredError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_REQUIRED,
            message,
            details=details,
            capability="execution",
            recoverable=True,
            remediation="Approve the exact command digest via the local CLI/API, then retry.",
        )


class ExecutionApprovalNotFoundError(CodeHarnessError):
    def __init__(self, approval_id: str) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_NOT_FOUND,
            "Execution approval was not found for the active project.",
            details={"approval_id": approval_id},
            capability="execution",
        )


class ExecutionApprovalInvalidError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_INVALID,
            message,
            details=details,
            capability="execution",
        )


class ExecutionApprovalExpiredError(CodeHarnessError):
    def __init__(self, approval_id: str) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_EXPIRED,
            "Execution approval has expired.",
            details={"approval_id": approval_id},
            capability="execution",
            recoverable=True,
            remediation="Request a new approval for the exact command.",
        )


class ExecutionApprovalConsumedError(CodeHarnessError):
    def __init__(self, approval_id: str) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_CONSUMED,
            "Execution approval has already been consumed.",
            details={"approval_id": approval_id},
            capability="execution",
            recoverable=True,
            remediation="Request and approve a new single-use approval.",
        )


class ExecutionApprovalDeniedError(CodeHarnessError):
    def __init__(self, approval_id: str) -> None:
        super().__init__(
            ErrorCode.EXECUTION_APPROVAL_DENIED,
            "Execution approval was denied.",
            details={"approval_id": approval_id},
            capability="execution",
        )


class ExecutionStoreUnavailableError(CodeHarnessError):
    def __init__(self, message: str = "Execution audit storage is unavailable.") -> None:
        super().__init__(
            ErrorCode.EXECUTION_STORE_UNAVAILABLE,
            message,
            capability="execution",
            remediation="Run code-harness doctor and verify CODE_HARNESS_EXECUTION_HOME.",
        )


class ExecutionElevatedSessionError(CodeHarnessError):
    def __init__(
        self,
        message: str = "Execution is blocked because the host session is elevated.",
    ) -> None:
        super().__init__(
            ErrorCode.EXECUTION_ELEVATED_SESSION,
            message,
            capability="execution",
            remediation=(
                "Run without elevation, or set execution_allow_elevated only after explicit review."
            ),
        )


class ExecutionNotFoundError(CodeHarnessError):
    def __init__(self, execution_id: str) -> None:
        super().__init__(
            ErrorCode.EXECUTION_NOT_FOUND,
            "Execution was not found in the active runtime.",
            details={"execution_id": execution_id},
            capability="execution",
            remediation="Use an execution ID returned by this running code-harness instance.",
        )


class ExecutionConcurrencyLimitError(CodeHarnessError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            ErrorCode.EXECUTION_CONCURRENCY_LIMIT,
            "The project execution concurrency limit is already occupied.",
            details={"limit": limit},
            capability="execution",
            recoverable=True,
            remediation="Wait for an active execution to finish or be cancelled, then retry.",
        )


class InvalidExecutionRequestError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.INVALID_EXECUTION_REQUEST,
            message,
            details=details,
            capability="execution",
        )


class ProcessStartError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.PROCESS_START_FAILED,
            message,
            details=details,
            capability="execution",
        )


class PowerShellExecutionDisabledError(CodeHarnessError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.POWERSHELL_EXECUTION_DISABLED,
            "PowerShell execution is disabled.",
            capability="execution",
            remediation=(
                "Set CODE_HARNESS_EXECUTION_POWERSHELL=1 after reviewing "
                "the PowerShell execution trust boundary."
            ),
        )


class PowerShellUnavailableError(CodeHarnessError):
    def __init__(self, executable: str, *, reason: str | None = None) -> None:
        details = {"executable": executable}
        if reason is not None:
            details["reason"] = reason
        super().__init__(
            ErrorCode.POWERSHELL_UNAVAILABLE,
            "PowerShell 7 or later is unavailable for inspection or execution.",
            details=details,
            capability="execution",
            remediation="Install PowerShell 7 and configure CODE_HARNESS_POWERSHELL if needed.",
        )


class PowerShellParseError(CodeHarnessError):
    def __init__(self, diagnostics: tuple[str, ...]) -> None:
        super().__init__(
            ErrorCode.POWERSHELL_PARSE_FAILED,
            "PowerShell script could not be parsed.",
            details={"diagnostics": list(diagnostics)},
            capability="execution",
        )


class PowerShellAnalysisError(CodeHarnessError):
    def __init__(self, message: str) -> None:
        super().__init__(
            ErrorCode.POWERSHELL_ANALYSIS_FAILED,
            message,
            capability="execution",
            remediation="Retry inspection after verifying the local PowerShell installation.",
        )


class ChangeSetNotFoundError(CodeHarnessError):
    def __init__(self, change_set_id: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SET_NOT_FOUND,
            "Change set was not found.",
            details={"change_set_id": change_set_id},
            capability="review",
            remediation="Create a fresh change set with get_change_set.",
        )


class InvalidChangeRequestError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.INVALID_CHANGE_REQUEST,
            message,
            details=details,
            capability="review",
        )


class GitUnavailableError(CodeHarnessError):
    def __init__(self, message: str = "Git is unavailable on this host.") -> None:
        super().__init__(
            ErrorCode.GIT_UNAVAILABLE,
            message,
            capability="review",
            remediation="Install Git and ensure it is available on PATH.",
            recoverable=True,
        )


class GitCommandFailedError(CodeHarnessError):
    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(
            ErrorCode.GIT_COMMAND_FAILED,
            message,
            details={"stderr": stderr} if stderr else None,
            capability="review",
            recoverable=True,
        )


class ReviewActionsDisabledError(CodeHarnessError):
    def __init__(self, message: str = "Review actions are disabled.") -> None:
        super().__init__(
            ErrorCode.REVIEW_ACTIONS_DISABLED,
            message,
            capability="review",
            remediation=(
                "Enable CODE_HARNESS_REVIEW_ACTIONS and the specific action flag before use."
            ),
        )


class ReviewActionNotAllowedError(CodeHarnessError):
    def __init__(self, action: str) -> None:
        super().__init__(
            ErrorCode.REVIEW_ACTION_NOT_ALLOWED,
            f"Review action {action!r} is not allowed by configuration.",
            details={"action": action},
            capability="review",
            remediation=f"Enable the configuration flag that allows {action}.",
        )


class WorkspaceSnapshotMismatchError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.WORKSPACE_SNAPSHOT_MISMATCH,
            message,
            details=details,
            capability="review",
            remediation="Recapture the change set and request a fresh approval.",
        )


class ReviewPatchApplyFailedError(CodeHarnessError):
    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(
            ErrorCode.REVIEW_PATCH_APPLY_FAILED,
            message,
            details={"stderr": stderr} if stderr else None,
            capability="review",
            recoverable=True,
        )


class ChangeSessionNotFoundError(CodeHarnessError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_NOT_FOUND,
            "Change session was not found.",
            details={"session_id": session_id},
            capability="change_session",
        )


class ChangeSessionInvalidStateError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_INVALID_STATE,
            message,
            details=details,
            capability="change_session",
        )


class ChangeSessionUnsupportedTopologyError(CodeHarnessError):
    def __init__(self, topology_kind: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_UNSUPPORTED_TOPOLOGY,
            (
                f"Change session topology {topology_kind!r} is not supported "
                "in this release (single_git only)."
            ),
            details={"topology_kind": topology_kind},
            capability="change_session",
        )


class ChangeSessionWorkspaceDirtyError(CodeHarnessError):
    def __init__(self, message: str = "Workspace working tree is dirty.", **details: Any) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_WORKSPACE_DIRTY,
            message,
            details=details,
            capability="change_session",
            remediation="Commit or stash local changes before starting an isolated session.",
        )


class ChangeSessionStaleError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_STALE,
            message,
            details=details,
            capability="change_session",
            remediation="Create a new change session from the current workspace state.",
        )


class ChangeSessionConflictError(CodeHarnessError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_CONFLICT,
            message,
            details=details,
            capability="change_session",
            recoverable=True,
        )


class ChangeSessionDigestMismatchError(CodeHarnessError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_DIGEST_MISMATCH,
            "Candidate digest does not match the prepared change session.",
            details={"session_id": session_id},
            capability="change_session",
            remediation="Re-prepare the session and approve the new digest.",
        )


class ChangeSessionLockHeldError(CodeHarnessError):
    def __init__(self, lock_key: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_LOCK_HELD,
            "Another change session already holds the write lock.",
            details={"lock_key": lock_key},
            capability="change_session",
            recoverable=True,
        )


class ChangeSessionPathRejectedError(CodeHarnessError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_PATH_REJECTED,
            f"Path rejected for change-session operation: {reason}",
            details={"path": path, "reason": reason},
            capability="change_session",
        )


class ChangeSessionStoreUnavailableError(CodeHarnessError):
    def __init__(self, message: str = "Change session store is unavailable.") -> None:
        super().__init__(
            ErrorCode.CHANGE_SESSION_STORE_UNAVAILABLE,
            message,
            capability="change_session",
        )


def is_recoverable_error(error: BaseException) -> bool:
    return isinstance(error, CodeHarnessError) and error.recoverable
