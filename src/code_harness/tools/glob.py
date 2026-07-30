"""Glob: list files matching a pattern, most recently modified first."""

from __future__ import annotations

from pathlib import Path

from code_harness import ripgrep
from code_harness.paths import PathGuard


def glob(
    guard: PathGuard,
    *,
    glob_pattern: str,
    target_directory: str | None = None,
) -> str:
    pattern = glob_pattern if glob_pattern.startswith("**/") else f"**/{glob_pattern}"
    directory = guard.resolve(target_directory or ".", kind="directory")

    output = ripgrep.run(
        ["--files", "--hidden", "--glob", "!.git/", "--glob", pattern, "--", "."],
        cwd=directory,
    )
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    if not paths:
        return f"No files found matching '{glob_pattern}'."

    absolute = [directory / entry for entry in paths]
    absolute.sort(key=_modified_at, reverse=True)

    listing = "\n".join(f"- {guard.relative(entry)}" for entry in absolute)
    location = guard.relative(directory)
    return f"Result of search in '{location}' (total {len(absolute)} files):\n{listing}"


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
