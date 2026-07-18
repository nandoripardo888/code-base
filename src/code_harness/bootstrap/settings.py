import os
from dataclasses import dataclass
from pathlib import Path

from code_harness.domain.errors import ProjectNotFoundError


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    ripgrep_executable: str = "rg"
    ripgrep_timeout_seconds: float = 10.0
    max_file_size_bytes: int = 2_000_000

    @classmethod
    def for_root(cls, root: str | Path) -> "Settings":
        resolved = Path(root).expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise ProjectNotFoundError(str(root))
        return cls(
            root=resolved,
            ripgrep_executable=os.environ.get("CODE_HARNESS_RG", "rg"),
        )
