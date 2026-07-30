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
    APPLY_PATCH = "apply_patch"
    PUBLISH_REVIEW = "publish_review"
    CREATE_COMMIT = "create_commit"


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


class ApprovalChannel(StrEnum):
    DISABLED = "disabled"
    MCP_ELICITATION = "mcp_elicitation"
    HOST_LOOPBACK = "host_loopback"


class ApprovalDecisionSource(StrEnum):
    LOCAL_ADMIN = "local_admin"
    MCP_ELICITATION = "mcp_elicitation"
    HOST_LOOPBACK = "host_loopback"


class ApprovalBinding(StrEnum):
    NONE = "none"
    MCP_SESSION = "mcp_session"
    HOST_INSTANCE = "host_instance"


class HumanDecisionOutcome(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


class ChangeSessionStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    AGENT_WORKING = "agent_working"
    REVIEW_PENDING = "review_pending"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    STALE = "stale"
    FAILED = "failed"
    EXPIRED = "expired"
    CLEANING = "cleaning"
    CLEANED = "cleaned"


class ChangeSegmentKind(StrEnum):
    GIT_WORKTREE = "git_worktree"
    WORKSPACE_MIRROR = "workspace_mirror"


class WorkspaceTopologyKind(StrEnum):
    SINGLE_GIT = "single_git"
    NON_GIT = "non_git"
    COMPOSITE = "composite"


class ChangeIsolationMode(StrEnum):
    AUTO = "auto"
    ISOLATED = "isolated"
    IN_PLACE = "in_place"


class ChangeSourceKind(StrEnum):
    WORKING_TREE = "working_tree"
    STAGED = "staged"
    COMMIT = "commit"
    COMMIT_RANGE = "commit_range"
    BRANCH_COMPARE = "branch_compare"
    PULL_REQUEST = "pull_request"
    PATCH_FILE = "patch_file"


class FileChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    BINARY = "binary"


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
    CHANGE_SET_NOT_FOUND = "change_set_not_found"
    INVALID_CHANGE_REQUEST = "invalid_change_request"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_COMMAND_FAILED = "git_command_failed"
    REVIEW_ACTIONS_DISABLED = "review_actions_disabled"
    REVIEW_ACTION_NOT_ALLOWED = "review_action_not_allowed"
    WORKSPACE_SNAPSHOT_MISMATCH = "workspace_snapshot_mismatch"
    REVIEW_PATCH_APPLY_FAILED = "review_patch_apply_failed"
    CHANGE_SESSION_NOT_FOUND = "change_session_not_found"
    CHANGE_SESSION_INVALID_STATE = "change_session_invalid_state"
    CHANGE_SESSION_UNSUPPORTED_TOPOLOGY = "change_session_unsupported_topology"
    CHANGE_SESSION_WORKSPACE_DIRTY = "change_session_workspace_dirty"
    CHANGE_SESSION_STALE = "change_session_stale"
    CHANGE_SESSION_CONFLICT = "change_session_conflict"
    CHANGE_SESSION_DIGEST_MISMATCH = "change_session_digest_mismatch"
    CHANGE_SESSION_LOCK_HELD = "change_session_lock_held"
    CHANGE_SESSION_PATH_REJECTED = "change_session_path_rejected"
    CHANGE_SESSION_STORE_UNAVAILABLE = "change_session_store_unavailable"
    CHANGE_PATCH_INVALID = "change_patch_invalid"
    CHANGE_PATCH_CONTEXT_MISMATCH = "change_patch_context_mismatch"
    CHANGE_PATCH_BINARY_UNSUPPORTED = "change_patch_binary_unsupported"
    CHANGE_CHECKPOINT_NOT_FOUND = "change_checkpoint_not_found"
    CHANGE_CHECKPOINT_STALE = "change_checkpoint_stale"
    CHANGE_CHECKPOINT_UNAVAILABLE = "change_checkpoint_unavailable"
