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


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    index_path: Path
    ripgrep_executable: str = "rg"
    ripgrep_timeout_seconds: float = 10.0
    max_file_size_bytes: int = 2_000_000
    parsers_enabled: bool = True
    parser_timeout_seconds: float = 10.0
    parser_failure_threshold: int = 3
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

    def __post_init__(self) -> None:
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

    @property
    def project(self) -> Project:
        identity = os.path.normcase(str(self.root)).encode("utf-8")
        return Project(sha256(identity).hexdigest()[:32], str(self.root))

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
        return cls(
            root=resolved,
            index_path=configured_index.resolve(strict=False),
            ripgrep_executable=resolve_ripgrep_executable(),
            parsers_enabled=os.environ.get("CODE_HARNESS_PARSERS", "1").casefold()
            not in {"0", "false", "off", "no"},
            parser_timeout_seconds=float(
                os.environ.get("CODE_HARNESS_PARSER_TIMEOUT_SECONDS", "10")
            ),
            semantic_enabled=os.environ.get("CODE_HARNESS_SEMANTIC", "0").casefold()
            in {"1", "true", "on", "yes"},
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
            mcp_expose_index_commands=os.environ.get(
                "CODE_HARNESS_MCP_EXPOSE_INDEX", "0"
            ).casefold()
            in {"1", "true", "on", "yes"},
        )
