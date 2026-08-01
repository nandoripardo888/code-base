"""Glob: list files matching a pattern, most recently modified first."""

from __future__ import annotations

from code_harness.paths import PathGuard
from code_harness.tools.search_core import list_files
from code_harness.tools.search_globs import GlobInput


def glob(
    guard: PathGuard,
    *,
    glob_pattern: GlobInput,
    target_directory: str | None = None,
    include_all: bool = False,
) -> str:
    return list_files(
        guard,
        glob_pattern=glob_pattern,
        target_directory=target_directory,
        include_all=include_all,
    )
