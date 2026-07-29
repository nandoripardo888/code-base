import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from code_harness.domain.enums import ApprovalChannel, ChangeIsolationMode
from code_harness.domain.errors import ProjectNotFoundError
from code_harness.domain.models.project import Project
from code_harness.infrastructure.ripgrep.discovery import resolve_ripgrep_executable

_LOGGER = logging.getLogger(__name__)


def _default_model_cache() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "code-harness" / "models"


def _code_harness_home() -> Path:
    override = os.environ.get("CODE_HARNESS_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "code-harness"
    return Path.home() / ".code-harness"


def _default_execution_home() -> Path:
    override = os.environ.get("CODE_HARNESS_EXECUTION_HOME")
    if override:
        return Path(override).expanduser()
    return _code_harness_home() / "executions"


def _default_change_session_home() -> Path:
    override = os.environ.get("CODE_HARNESS_CHANGE_SESSION_HOME")
    if override:
        return Path(override).expanduser()
    return _code_harness_home() / "change-sessions"


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).casefold() in {"1", "true", "on", "yes"}


def _service_started_at() -> str:
    return datetime.now(UTC).isoformat()


def _service_instance_id() -> str:
    return uuid4().hex


def _build_commit() -> str | None:
    value = os.environ.get("CODE_HARNESS_BUILD_COMMIT", "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    index_path: Path
    ripgrep_executable: str = "rg"
    ripgrep_timeout_seconds: float = 10.0
    max_file_size_bytes: int = 2_000_000
    parsers_enabled: bool = True
    parser_timeout_seconds: float = 10.0
    parser_workers: int = 1
    parser_failure_threshold: int = 3
    parser_failure_window_seconds: float = 60.0
    parser_circuit_reset_seconds: float = 60.0
    chunk_target_chars: int = 4_000
    chunk_max_chars: int = 8_000
    semantic_enabled: bool = False
    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_batch_size: int = 16
    embedding_window_chars: int = 1_500
    embedding_window_overlap_chars: int = 150
    embedding_cache_path: Path = field(default_factory=_default_model_cache)
    embedding_timeout_seconds: float = 300.0
    system_trust_enabled: bool = True
    ca_bundle_path: Path | None = None
    mcp_expose_index_commands: bool = False
    execution_enabled: bool = False
    execution_backend: str = "host_supervised"
    execution_powershell_enabled: bool = False
    execution_powershell_executable: str = "pwsh"
    execution_require_approval: bool = True
    execution_default_timeout_seconds: float = 60.0
    execution_max_timeout_seconds: float = 1_800.0
    execution_max_output_bytes: int = 200_000
    execution_max_processes: int = 32
    execution_max_concurrent: int = 1
    execution_approval_ttl_seconds: int = 600
    execution_allow_elevated: bool = False
    execution_home: Path = field(default_factory=_default_execution_home)
    mcp_expose_execution: bool = False
    mcp_expose_powershell: bool = False
    review_actions_enabled: bool = False
    mcp_expose_review_actions: bool = False
    review_allow_apply: bool = False
    review_allow_commit: bool = False
    review_allow_publish: bool = False
    review_use_worktree: bool = False
    review_publish_dir: Path | None = None
    mcp_execution_approval_channel: ApprovalChannel = ApprovalChannel.DISABLED
    mcp_execution_elicitation_enabled: bool = False
    mcp_execution_elicitation_trust_mode: str = "disabled"
    mcp_execution_elicitation_timeout_seconds: float = 120.0
    host_loopback_port: int | None = None
    host_loopback_open_browser: bool = True
    change_isolation: ChangeIsolationMode = ChangeIsolationMode.AUTO
    change_session_home: Path = field(default_factory=_default_change_session_home)
    change_require_clean_git: bool = True
    change_conflict_retention_hours: int = 168
    change_abandoned_retention_hours: int = 24
    change_failed_preparation_retention_hours: int = 1
    change_audit_retention_days: int = 30
    change_gc_interval_minutes: int = 60
    change_max_session_bytes: int = 5_000_000_000
    change_max_total_bytes: int = 20_000_000_000
    mcp_expose_change_sessions: bool = False
    build_commit: str | None = field(default_factory=_build_commit)
    service_started_at: str = field(default_factory=_service_started_at)
    service_instance_id: str = field(default_factory=_service_instance_id)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mcp_execution_approval_channel",
            _coerce_approval_channel(self.mcp_execution_approval_channel),
        )
        if not 1 <= self.parser_workers <= 8:
            raise ValueError("parser_workers must be between 1 and 8")
        if self.parser_timeout_seconds <= 0:
            raise ValueError("parser_timeout_seconds must be greater than zero")
        if self.parser_failure_threshold <= 0:
            raise ValueError("parser_failure_threshold must be greater than zero")
        if self.parser_failure_window_seconds <= 0:
            raise ValueError("parser_failure_window_seconds must be greater than zero")
        if self.parser_circuit_reset_seconds <= 0:
            raise ValueError("parser_circuit_reset_seconds must be greater than zero")
        if self.embedding_batch_size <= 0:
            raise ValueError("embedding_batch_size must be greater than zero")
        if self.embedding_window_chars <= 0:
            raise ValueError("embedding_window_chars must be greater than zero")
        if not 0 <= self.embedding_window_overlap_chars < self.embedding_window_chars:
            raise ValueError(
                "embedding_window_overlap_chars must be non-negative and smaller than the window"
            )
        if self.embedding_timeout_seconds <= 0:
            raise ValueError("embedding_timeout_seconds must be greater than zero")
        if self.execution_backend not in {"host_supervised", "windows_sandbox"}:
            raise ValueError("execution_backend must be one of: host_supervised, windows_sandbox")
        if self.execution_default_timeout_seconds <= 0:
            raise ValueError("execution_default_timeout_seconds must be greater than zero")
        if self.execution_max_timeout_seconds <= 0:
            raise ValueError("execution_max_timeout_seconds must be greater than zero")
        if self.execution_default_timeout_seconds > self.execution_max_timeout_seconds:
            raise ValueError(
                "execution_default_timeout_seconds must not exceed execution_max_timeout_seconds"
            )
        if self.execution_max_output_bytes <= 0:
            raise ValueError("execution_max_output_bytes must be greater than zero")
        if not 1 <= self.execution_max_processes <= 32:
            raise ValueError("execution_max_processes must be between 1 and 32")
        if not 1 <= self.execution_max_concurrent <= 32:
            raise ValueError("execution_max_concurrent must be between 1 and 32")
        if self.execution_approval_ttl_seconds <= 0:
            raise ValueError("execution_approval_ttl_seconds must be greater than zero")
        if self.mcp_expose_execution and not self.execution_enabled:
            raise ValueError("mcp_expose_execution requires execution_enabled")
        if self.review_actions_enabled and not self.execution_enabled:
            raise ValueError("review_actions_enabled requires execution_enabled")
        if self.mcp_expose_review_actions and not self.review_actions_enabled:
            raise ValueError("mcp_expose_review_actions requires review_actions_enabled")
        if self.review_allow_apply and not self.review_actions_enabled:
            raise ValueError("review_allow_apply requires review_actions_enabled")
        if self.review_allow_commit and not self.review_actions_enabled:
            raise ValueError("review_allow_commit requires review_actions_enabled")
        if self.review_allow_publish and not self.review_actions_enabled:
            raise ValueError("review_allow_publish requires review_actions_enabled")
        if self.mcp_expose_powershell and not (
            self.execution_enabled
            and self.execution_powershell_enabled
            and self.mcp_expose_execution
        ):
            raise ValueError(
                "mcp_expose_powershell requires execution_enabled and "
                "execution_powershell_enabled and mcp_expose_execution"
            )
        if (
            self.mcp_execution_approval_channel is not ApprovalChannel.DISABLED
            and not self.mcp_expose_execution
        ):
            raise ValueError("mcp_execution_approval_channel requires mcp_expose_execution")
        if self.mcp_execution_elicitation_enabled and not self.mcp_expose_execution:
            raise ValueError("mcp_execution_elicitation_enabled requires mcp_expose_execution")
        if self.mcp_execution_elicitation_trust_mode not in {
            "disabled",
            "local_interactive",
        }:
            raise ValueError(
                "mcp_execution_elicitation_trust_mode must be one of: disabled, local_interactive"
            )
        if (
            self.mcp_execution_elicitation_enabled
            and self.mcp_execution_elicitation_trust_mode != "local_interactive"
        ):
            raise ValueError("MCP execution elicitation requires trust mode local_interactive")
        if self.mcp_execution_elicitation_timeout_seconds <= 0:
            raise ValueError("mcp_execution_elicitation_timeout_seconds must be greater than zero")
        if self.host_loopback_port is not None and not 1 <= self.host_loopback_port <= 65535:
            raise ValueError("host_loopback_port must be between 1 and 65535")
        if self.execution_powershell_enabled and not self.execution_enabled:
            raise ValueError("execution_powershell_enabled requires execution_enabled")
        object.__setattr__(
            self,
            "change_isolation",
            _coerce_change_isolation(self.change_isolation),
        )
        if self.change_conflict_retention_hours <= 0:
            raise ValueError("change_conflict_retention_hours must be greater than zero")
        if self.change_abandoned_retention_hours <= 0:
            raise ValueError("change_abandoned_retention_hours must be greater than zero")
        if self.change_failed_preparation_retention_hours <= 0:
            raise ValueError("change_failed_preparation_retention_hours must be greater than zero")
        if self.change_audit_retention_days <= 0:
            raise ValueError("change_audit_retention_days must be greater than zero")
        if self.change_gc_interval_minutes <= 0:
            raise ValueError("change_gc_interval_minutes must be greater than zero")
        if self.change_max_session_bytes <= 0:
            raise ValueError("change_max_session_bytes must be greater than zero")
        if self.change_max_total_bytes <= 0:
            raise ValueError("change_max_total_bytes must be greater than zero")
        _sync_legacy_elicitation_fields(self)

    @property
    def project(self) -> Project:
        identity = os.path.normcase(str(self.root)).encode("utf-8")
        return Project(sha256(identity).hexdigest()[:32], str(self.root))

    def execution_project_home(self) -> Path:
        return self.execution_home / self.project.project_id

    def execution_store_path(self) -> Path:
        return self.execution_project_home() / "execution.db"

    def change_sessions_db_path(self) -> Path:
        return _code_harness_home() / "change-sessions.db"

    def change_blobs_home(self) -> Path:
        return _code_harness_home() / "blobs"

    def change_locks_home(self) -> Path:
        return _code_harness_home() / "locks"

    @classmethod
    def for_root(cls, root: str | Path) -> "Settings":
        resolved = Path(root).expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise ProjectNotFoundError(str(root))
        configured_index = Path(
            os.environ.get("CODE_HARNESS_INDEX_PATH", ".code-harness/index.db")
        ).expanduser()
        if not configured_index.is_absolute():
            configured_index = resolved / configured_index
        configured_cache = Path(
            os.environ.get("CODE_HARNESS_MODEL_CACHE", str(_default_model_cache()))
        ).expanduser()
        configured_ca = os.environ.get("CODE_HARNESS_CA_BUNDLE")
        configured_execution_home = _default_execution_home().expanduser().resolve(strict=False)
        configured_review_publish = os.environ.get("CODE_HARNESS_REVIEW_PUBLISH_DIR")
        return cls(
            root=resolved,
            index_path=configured_index.resolve(strict=False),
            ripgrep_executable=resolve_ripgrep_executable(),
            parsers_enabled=os.environ.get("CODE_HARNESS_PARSERS", "1").casefold()
            not in {"0", "false", "off", "no"},
            parser_timeout_seconds=float(
                os.environ.get("CODE_HARNESS_PARSER_TIMEOUT_SECONDS", "10")
            ),
            parser_workers=int(
                os.environ.get(
                    "CODE_HARNESS_PARSER_WORKERS",
                    str(min(4, os.cpu_count() or 1)),
                )
            ),
            parser_failure_threshold=int(
                os.environ.get("CODE_HARNESS_PARSER_FAILURE_THRESHOLD", "3")
            ),
            parser_failure_window_seconds=float(
                os.environ.get("CODE_HARNESS_PARSER_FAILURE_WINDOW_SECONDS", "60")
            ),
            parser_circuit_reset_seconds=float(
                os.environ.get("CODE_HARNESS_PARSER_CIRCUIT_RESET_SECONDS", "60")
            ),
            semantic_enabled=_env_flag("CODE_HARNESS_SEMANTIC"),
            embedding_provider=os.environ.get("CODE_HARNESS_EMBEDDING_PROVIDER", "local"),
            embedding_model=os.environ.get(
                "CODE_HARNESS_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            embedding_batch_size=int(os.environ.get("CODE_HARNESS_EMBEDDING_BATCH_SIZE", "16")),
            embedding_window_chars=int(
                os.environ.get("CODE_HARNESS_EMBEDDING_WINDOW_CHARS", "1500")
            ),
            embedding_window_overlap_chars=int(
                os.environ.get("CODE_HARNESS_EMBEDDING_WINDOW_OVERLAP_CHARS", "150")
            ),
            embedding_cache_path=configured_cache.resolve(strict=False),
            embedding_timeout_seconds=float(
                os.environ.get("CODE_HARNESS_EMBEDDING_TIMEOUT_SECONDS", "300")
            ),
            system_trust_enabled=os.environ.get("CODE_HARNESS_SYSTEM_TRUST", "1").casefold()
            not in {"0", "false", "off", "no"},
            ca_bundle_path=(
                Path(configured_ca).expanduser().resolve(strict=False) if configured_ca else None
            ),
            mcp_expose_index_commands=_env_flag("CODE_HARNESS_MCP_EXPOSE_INDEX"),
            execution_enabled=_env_flag("CODE_HARNESS_EXECUTION"),
            execution_backend=os.environ.get("CODE_HARNESS_EXECUTION_BACKEND", "host_supervised"),
            execution_powershell_enabled=_env_flag("CODE_HARNESS_EXECUTION_POWERSHELL"),
            execution_powershell_executable=os.environ.get("CODE_HARNESS_POWERSHELL", "pwsh"),
            execution_require_approval=os.environ.get(
                "CODE_HARNESS_EXECUTION_REQUIRE_APPROVAL", "1"
            ).casefold()
            not in {"0", "false", "off", "no"},
            execution_default_timeout_seconds=float(
                os.environ.get("CODE_HARNESS_EXECUTION_DEFAULT_TIMEOUT_SECONDS", "60")
            ),
            execution_max_timeout_seconds=float(
                os.environ.get("CODE_HARNESS_EXECUTION_MAX_TIMEOUT_SECONDS", "1800")
            ),
            execution_max_output_bytes=int(
                os.environ.get("CODE_HARNESS_EXECUTION_MAX_OUTPUT_BYTES", "200000")
            ),
            execution_max_processes=int(
                os.environ.get("CODE_HARNESS_EXECUTION_MAX_PROCESSES", "32")
            ),
            execution_max_concurrent=int(
                os.environ.get("CODE_HARNESS_EXECUTION_MAX_CONCURRENT", "1")
            ),
            execution_approval_ttl_seconds=int(
                os.environ.get("CODE_HARNESS_EXECUTION_APPROVAL_TTL_SECONDS", "600")
            ),
            execution_allow_elevated=_env_flag("CODE_HARNESS_EXECUTION_ALLOW_ELEVATED"),
            execution_home=configured_execution_home,
            mcp_expose_execution=_env_flag("CODE_HARNESS_MCP_EXPOSE_EXECUTION"),
            mcp_expose_powershell=_env_flag("CODE_HARNESS_MCP_EXPOSE_POWERSHELL"),
            review_actions_enabled=_env_flag("CODE_HARNESS_REVIEW_ACTIONS"),
            mcp_expose_review_actions=_env_flag("CODE_HARNESS_MCP_EXPOSE_REVIEW_ACTIONS"),
            review_allow_apply=_env_flag("CODE_HARNESS_REVIEW_ALLOW_APPLY"),
            review_allow_commit=_env_flag("CODE_HARNESS_REVIEW_ALLOW_COMMIT"),
            review_allow_publish=_env_flag("CODE_HARNESS_REVIEW_ALLOW_PUBLISH"),
            review_use_worktree=_env_flag("CODE_HARNESS_REVIEW_USE_WORKTREE"),
            review_publish_dir=(
                Path(configured_review_publish).expanduser().resolve(strict=False)
                if configured_review_publish
                else None
            ),
            mcp_execution_approval_channel=_resolve_approval_channel_from_env(),
            mcp_execution_elicitation_enabled=_env_flag("CODE_HARNESS_MCP_EXECUTION_ELICITATION"),
            mcp_execution_elicitation_trust_mode=os.environ.get(
                "CODE_HARNESS_MCP_EXECUTION_ELICITATION_TRUST_MODE",
                "disabled",
            ),
            mcp_execution_elicitation_timeout_seconds=float(
                os.environ.get(
                    "CODE_HARNESS_MCP_EXECUTION_ELICITATION_TIMEOUT_SECONDS",
                    "120",
                )
            ),
            host_loopback_port=_optional_int_env("CODE_HARNESS_HOST_LOOPBACK_PORT"),
            host_loopback_open_browser=os.environ.get(
                "CODE_HARNESS_HOST_LOOPBACK_OPEN_BROWSER",
                "1",
            ).casefold()
            not in {"0", "false", "off", "no"},
            change_isolation=_coerce_change_isolation(
                os.environ.get("CODE_HARNESS_CHANGE_ISOLATION", "auto")
            ),
            change_session_home=_default_change_session_home().expanduser().resolve(strict=False),
            change_require_clean_git=os.environ.get(
                "CODE_HARNESS_CHANGE_REQUIRE_CLEAN_GIT",
                "1",
            ).casefold()
            not in {"0", "false", "off", "no"},
            change_conflict_retention_hours=int(
                os.environ.get("CODE_HARNESS_CHANGE_CONFLICT_RETENTION_HOURS", "168")
            ),
            change_abandoned_retention_hours=int(
                os.environ.get("CODE_HARNESS_CHANGE_ABANDONED_RETENTION_HOURS", "24")
            ),
            change_failed_preparation_retention_hours=int(
                os.environ.get("CODE_HARNESS_CHANGE_FAILED_PREPARATION_RETENTION_HOURS", "1")
            ),
            change_audit_retention_days=int(
                os.environ.get("CODE_HARNESS_CHANGE_AUDIT_RETENTION_DAYS", "30")
            ),
            change_gc_interval_minutes=int(
                os.environ.get("CODE_HARNESS_CHANGE_GC_INTERVAL_MINUTES", "60")
            ),
            change_max_session_bytes=int(
                os.environ.get("CODE_HARNESS_CHANGE_MAX_SESSION_BYTES", "5000000000")
            ),
            change_max_total_bytes=int(
                os.environ.get("CODE_HARNESS_CHANGE_MAX_TOTAL_BYTES", "20000000000")
            ),
            mcp_expose_change_sessions=_env_flag("CODE_HARNESS_MCP_EXPOSE_CHANGE_SESSIONS"),
        )


def _coerce_change_isolation(value: ChangeIsolationMode | str) -> ChangeIsolationMode:
    if isinstance(value, ChangeIsolationMode):
        return value
    try:
        return ChangeIsolationMode(value)
    except ValueError as error:
        raise ValueError(
            "change_isolation must be one of: "
            + ", ".join(item.value for item in ChangeIsolationMode)
        ) from error


def _optional_int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _coerce_approval_channel(value: ApprovalChannel | str) -> ApprovalChannel:
    if isinstance(value, ApprovalChannel):
        return value
    try:
        return ApprovalChannel(value)
    except ValueError as error:
        raise ValueError(
            "mcp_execution_approval_channel must be one of: "
            + ", ".join(item.value for item in ApprovalChannel)
        ) from error


def _legacy_approval_channel(
    *,
    elicitation_enabled: bool,
    trust_mode: str,
) -> ApprovalChannel:
    if elicitation_enabled and trust_mode == "local_interactive":
        return ApprovalChannel.MCP_ELICITATION
    return ApprovalChannel.DISABLED


def _resolve_approval_channel_from_env() -> ApprovalChannel:
    configured = os.environ.get("CODE_HARNESS_MCP_EXECUTION_APPROVAL_CHANNEL")
    if configured is not None and configured.strip():
        return _coerce_approval_channel(configured.strip())
    legacy_enabled = _env_flag("CODE_HARNESS_MCP_EXECUTION_ELICITATION")
    legacy_trust = os.environ.get(
        "CODE_HARNESS_MCP_EXECUTION_ELICITATION_TRUST_MODE",
        "disabled",
    )
    if legacy_enabled or legacy_trust != "disabled":
        _LOGGER.warning(
            "CODE_HARNESS_MCP_EXECUTION_ELICITATION* is deprecated; "
            "use CODE_HARNESS_MCP_EXECUTION_APPROVAL_CHANNEL "
            "(disabled|mcp_elicitation|host_loopback)."
        )
    return _legacy_approval_channel(
        elicitation_enabled=legacy_enabled,
        trust_mode=legacy_trust,
    )


def _sync_legacy_elicitation_fields(settings: Settings) -> None:
    """Keep legacy elicitation flags aligned with the approval channel."""
    channel = settings.mcp_execution_approval_channel
    if channel is ApprovalChannel.MCP_ELICITATION:
        object.__setattr__(settings, "mcp_execution_elicitation_enabled", True)
        object.__setattr__(settings, "mcp_execution_elicitation_trust_mode", "local_interactive")
        return
    if channel is ApprovalChannel.DISABLED:
        # Preserve explicitly configured legacy fields when channel was derived from them.
        if settings.mcp_execution_elicitation_enabled:
            object.__setattr__(
                settings,
                "mcp_execution_approval_channel",
                ApprovalChannel.MCP_ELICITATION,
            )
            object.__setattr__(
                settings,
                "mcp_execution_elicitation_trust_mode",
                "local_interactive",
            )
        return
    object.__setattr__(settings, "mcp_execution_elicitation_enabled", False)
    object.__setattr__(settings, "mcp_execution_elicitation_trust_mode", "disabled")
