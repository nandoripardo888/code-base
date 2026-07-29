from enum import StrEnum


class MatchType(StrEnum):
    EXACT = "exact"
    EXACT_LITERAL = "exact_literal"
    SUBSTRING = "substring"
    FTS_TERM = "fts_term"
    FTS_PHRASE = "fts_phrase"
    REGEX = "regex"
    FULL_TEXT = "full_text"
    SYMBOL = "symbol"
    REFERENCE = "reference"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    PATH = "path"


class QueryKind(StrEnum):
    EXACT = "exact"
    MIXED = "mixed"
    CONCEPTUAL = "conceptual"


class IndexMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    VERIFY = "verify"


class IndexState(StrEnum):
    NOT_INITIALIZED = "not_initialized"
    INDEXING = "indexing"
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    FAILED = "failed"
    REPAIRING = "repairing"


class ParseState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    READY = "ready"
    FALLBACK = "fallback"
    FAILED = "failed"


class DiagnosticStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class CapabilityState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class CommandKind(StrEnum):
    PROCESS = "process"
    POWERSHELL = "powershell"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"
    UNSUPPORTED = "unsupported"
    UNKNOWN_DYNAMIC_BEHAVIOR = "unknown_dynamic_behavior"


class ExecutionCapability(StrEnum):
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    EXECUTE_REPOSITORY_CODE = "execute_repository_code"
    PROCESS_SPAWN = "process_spawn"
    NETWORK_OUTBOUND = "network_outbound"
    CREDENTIAL_ACCESS = "credential_access"
    HOST_FILESYSTEM_READ = "host_filesystem_read"
    HOST_FILESYSTEM_WRITE = "host_filesystem_write"
    REGISTRY_READ = "registry_read"
    REGISTRY_WRITE = "registry_write"
    SERVICE_CONTROL = "service_control"
    ADMIN = "admin"


class ExecutionRiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class ExecutionState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "internal_error"
    PROJECT_NOT_FOUND = "project_not_found"
    PATH_OUTSIDE_PROJECT = "path_outside_project"
    FILE_NOT_FOUND = "file_not_found"
    INVALID_PATH_KIND = "invalid_path_kind"
    BINARY_FILE = "binary_file"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    RIPGREP_UNAVAILABLE = "ripgrep_unavailable"
    RIPGREP_TIMEOUT = "ripgrep_timeout"
    INDEX_NOT_READY = "index_not_ready"
    INDEX_CORRUPTED = "index_corrupted"
    PARSER_UNAVAILABLE = "parser_unavailable"
    PARSER_TIMEOUT = "parser_timeout"
    PARSER_CRASH = "parser_crash"
    PARSER_CIRCUIT_OPEN = "parser_circuit_open"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    INVALID_QUERY = "invalid_query"
    RESULT_LIMIT_EXCEEDED = "result_limit_exceeded"
    CURSOR_STALE = "cursor_stale"
    EXECUTION_DISABLED = "execution_disabled"
    EXECUTION_NOT_SUPPORTED = "execution_not_supported"
    EXECUTION_POLICY_DENIED = "execution_policy_denied"
    EXECUTION_APPROVAL_REQUIRED = "execution_approval_required"
    EXECUTION_APPROVAL_NOT_FOUND = "execution_approval_not_found"
    EXECUTION_APPROVAL_INVALID = "execution_approval_invalid"
    EXECUTION_APPROVAL_EXPIRED = "execution_approval_expired"
    EXECUTION_APPROVAL_CONSUMED = "execution_approval_consumed"
    EXECUTION_APPROVAL_DENIED = "execution_approval_denied"
    EXECUTION_STORE_UNAVAILABLE = "execution_store_unavailable"
    EXECUTION_ELEVATED_SESSION = "execution_elevated_session"
    EXECUTION_NOT_FOUND = "execution_not_found"
    EXECUTION_CONCURRENCY_LIMIT = "execution_concurrency_limit"
    INVALID_EXECUTION_REQUEST = "invalid_execution_request"
    POWERSHELL_EXECUTION_DISABLED = "powershell_execution_disabled"
    POWERSHELL_UNAVAILABLE = "powershell_unavailable"
    POWERSHELL_PARSE_FAILED = "powershell_parse_failed"
    POWERSHELL_ANALYSIS_FAILED = "powershell_analysis_failed"
    PROCESS_START_FAILED = "process_start_failed"
