from pathlib import Path

from code_harness.domain.errors import (
    InvalidPathKindError,
    PathOutsideProjectError,
    ProjectNotFoundError,
    SourceFileNotFoundError,
)


def _strip_extended_prefix(path: Path) -> Path:
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    return path


class PathGuard:
    def __init__(self, root: Path | str) -> None:
        candidate = Path(root).expanduser().resolve(strict=False)
        if not candidate.is_dir():
            raise ProjectNotFoundError(str(root))
        self._root = _strip_extended_prefix(candidate)

    @property
    def root(self) -> Path:
        return self._root

    def resolve_file(self, path: str) -> tuple[Path, str]:
        absolute, relative = self.resolve_within_root(
            path,
            expected_kind="file",
            must_exist=True,
        )
        return Path(absolute), relative

    def resolve_within_root(
        self,
        path: str,
        *,
        expected_kind: str = "directory",
        must_exist: bool = True,
    ) -> tuple[str, str]:
        if expected_kind not in {"file", "directory", "any"}:
            raise ValueError("expected_kind must be one of: file, directory, any")
        if not path or not str(path).strip():
            raise PathOutsideProjectError(path)

        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else self._root / supplied
        resolved = _strip_extended_prefix(candidate.resolve(strict=False))

        try:
            relative = resolved.relative_to(self._root)
        except ValueError as error:
            raise PathOutsideProjectError(path) from error

        relative_text = relative.as_posix()
        if relative_text == ".":
            relative_text = ""

        if must_exist:
            if expected_kind == "file":
                if not resolved.is_file():
                    if resolved.exists():
                        raise InvalidPathKindError(
                            path,
                            expected="file",
                            actual="directory" if resolved.is_dir() else "other",
                        )
                    raise SourceFileNotFoundError(path)
            elif expected_kind == "directory":
                if not resolved.is_dir():
                    if resolved.exists():
                        raise InvalidPathKindError(
                            path,
                            expected="directory",
                            actual="file" if resolved.is_file() else "other",
                        )
                    raise SourceFileNotFoundError(path)
            elif not resolved.exists():
                raise SourceFileNotFoundError(path)
        elif resolved.exists():
            if expected_kind == "file" and not resolved.is_file():
                raise InvalidPathKindError(
                    path,
                    expected="file",
                    actual="directory" if resolved.is_dir() else "other",
                )
            if expected_kind == "directory" and not resolved.is_dir():
                raise InvalidPathKindError(
                    path,
                    expected="directory",
                    actual="file" if resolved.is_file() else "other",
                )

        return str(resolved), relative_text or "."
