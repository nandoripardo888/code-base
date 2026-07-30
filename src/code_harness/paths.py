"""Project-root confinement for every path a tool touches."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from code_harness.errors import (
    InvalidPathKindError,
    PathNotFoundError,
    PathOutsideProjectError,
    ProjectNotFoundError,
)

PathKind = Literal["file", "directory", "any"]


def _strip_extended_prefix(path: Path) -> Path:
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    return path


class PathGuard:
    """Resolves user-supplied paths and rejects anything outside the allowed roots.

    ``additional_roots`` is reserved for exceptional cases that need a second
    sandbox root. Shell job logs are not exposed through it; use ``GetJobStatus``.
    """

    def __init__(self, root: Path | str, *, additional_roots: Iterable[Path | str] = ()) -> None:
        candidate = Path(root).expanduser().resolve(strict=False)
        if not candidate.is_dir():
            raise ProjectNotFoundError(str(root))
        self._root = _strip_extended_prefix(candidate)
        extra = tuple(
            _strip_extended_prefix(Path(entry).expanduser().resolve(strict=False))
            for entry in additional_roots
        )
        self._allowed = (self._root, *extra)

    @property
    def root(self) -> Path:
        return self._root

    def allow(self, directory: Path) -> None:
        """Register another root whose files the tools may touch."""
        resolved = _strip_extended_prefix(directory.expanduser().resolve(strict=False))
        if resolved not in self._allowed:
            self._allowed = (*self._allowed, resolved)

    def resolve(
        self,
        path: str,
        *,
        kind: PathKind = "any",
        must_exist: bool = True,
    ) -> Path:
        """Return the absolute path, guaranteed to live under the root."""
        if not path or not path.strip():
            raise PathOutsideProjectError(path)

        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else self._root / supplied
        resolved = _strip_extended_prefix(candidate.resolve(strict=False))

        if not any(resolved == root or root in resolved.parents for root in self._allowed):
            raise PathOutsideProjectError(path)

        exists = resolved.exists()
        if must_exist and not exists:
            raise PathNotFoundError(path)
        if exists and kind != "any":
            actual = "directory" if resolved.is_dir() else "file" if resolved.is_file() else "other"
            if actual != kind:
                raise InvalidPathKindError(path, expected=kind, actual=actual)
        return resolved

    def relative(self, path: Path) -> str:
        """Render a path relative to the root using forward slashes."""
        try:
            relative = path.relative_to(self._root).as_posix()
        except ValueError:
            return path.as_posix()
        return relative or "."
