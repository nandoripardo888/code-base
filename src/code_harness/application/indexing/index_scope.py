from __future__ import annotations

from dataclasses import dataclass, field

import pathspec


def _compile_globs(patterns: tuple[str, ...]) -> pathspec.PathSpec | None:
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


@dataclass(frozen=True, slots=True)
class IndexScope:
    """Temporary execution filters for partial indexing (not project ignore rules)."""

    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    _include: pathspec.PathSpec | None = field(default=None, init=False, repr=False, compare=False)
    _exclude: pathspec.PathSpec | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_include", _compile_globs(self.include_globs))
        object.__setattr__(self, "_exclude", _compile_globs(self.exclude_globs))

    @property
    def partial(self) -> bool:
        return bool(self.include_globs or self.exclude_globs)

    def matches(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        if self._include is not None and not self._include.match_file(normalized):
            return False
        return not (self._exclude is not None and self._exclude.match_file(normalized))
