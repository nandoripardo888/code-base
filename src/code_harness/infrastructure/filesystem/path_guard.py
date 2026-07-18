from pathlib import Path

from code_harness.domain.errors import (
    PathOutsideProjectError,
    ProjectNotFoundError,
    SourceFileNotFoundError,
)


class PathGuard:
    def __init__(self, root: Path | str) -> None:
        candidate = Path(root).expanduser().resolve(strict=False)
        if not candidate.is_dir():
            raise ProjectNotFoundError(str(root))
        self._root = candidate

    @property
    def root(self) -> Path:
        return self._root

    def resolve_file(self, path: str) -> tuple[Path, str]:
        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else self._root / supplied
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(self._root)
        except ValueError as error:
            raise PathOutsideProjectError(path) from error
        if not resolved.is_file():
            raise SourceFileNotFoundError(path)
        return resolved, relative.as_posix()
