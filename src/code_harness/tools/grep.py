"""Grep: regex search over the project, powered by ripgrep."""

from __future__ import annotations

from collections.abc import Sequence

from code_harness.paths import PathGuard
from code_harness.tools.search_core import (
    MATCH_CAP,
    OUTPUT_MODES,
    OutputMode,
    grep_content,
)
from code_harness.tools.search_globs import GlobInput

__all__ = ["MATCH_CAP", "OUTPUT_MODES", "OutputMode", "grep"]


def grep(
    guard: PathGuard,
    *,
    pattern: str | None = None,
    path: str | None = None,
    glob: GlobInput | None = None,
    file_type: str | None = None,
    output_mode: str = "content",
    case_insensitive: bool = False,
    context_after: int | None = None,
    context_before: int | None = None,
    context_lines: int | None = None,
    multiline: bool = False,
    head_limit: int | None = None,
    offset: int | None = None,
    include_all: bool = False,
    exclude: GlobInput | None = None,
    reference_kind: str | Sequence[str] | None = None,
    exclude_reference_kind: str | Sequence[str] | None = None,
) -> str:
    return grep_content(
        guard,
        pattern=pattern,
        path=path,
        glob=glob,
        file_type=file_type,
        output_mode=output_mode,
        case_insensitive=case_insensitive,
        context_after=context_after,
        context_before=context_before,
        context_lines=context_lines,
        multiline=multiline,
        head_limit=head_limit,
        offset=offset,
        include_all=include_all,
        exclude=exclude,
        reference_kind=reference_kind,
        exclude_reference_kind=exclude_reference_kind,
    )
