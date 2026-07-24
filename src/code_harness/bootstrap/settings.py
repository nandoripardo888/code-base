import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from code_harness.domain.errors import ProjectNotFoundError
from code_harness.domain.models.project import Project
from code_harness.infrastructure.ripgrep.discovery import resolve_ripgrep_executable


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


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).casefold() in {"1", "true", "on", "yes"}


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
    execution_backend: str = "host"
    execution_require_approval: bool = True
    execution_default_timeout_seconds: float = 60.0
    execution_max_timeout_seconds: float = 1_800.0
    execution_max_output_bytes: int = 200_000
    execution_allow_elevated: bool = False
    execution_home: Path = field(default_factory=_default_execution_home)
    mcp_expose_execution: bool = False

    def __post_init__(self) -> None:
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
        if self.execution_backend not in {"host", "host_supervised", "windows_sandbox"}:
            raise ValueError(
                "execution_backend must be one of: host, host_supervised, windows_sandbox"
            )
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
        if self.mcp_expose_execution and not self.execution_enabled:
            raise ValueError("mcp_expose_execution requires execution_enabled")

    @property
    def project(self) -> Project:
        identity = os.path.normcase(str(self.root)).encode("utf-8")
        return Project(sha256(identity).hexdigest()[:32], str(self.root))

    def execution_project_home(self) -> Path:
        return self.execution_home / self.project.project_id

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
            execution_backend=os.environ.get("CODE_HARNESS_EXECUTION_BACKEND", "host"),
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
            execution_allow_elevated=_env_flag("CODE_HARNESS_EXECUTION_ALLOW_ELEVATED"),
            execution_home=configured_execution_home,
            mcp_expose_execution=_env_flag("CODE_HARNESS_MCP_EXPOSE_EXECUTION"),
        )
