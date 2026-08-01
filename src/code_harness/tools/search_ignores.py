"""Harness ignore layers for Grep/Glob (source-first defaults).

Ripgrep already honours ``.gitignore``. This module adds harness defaults and an
optional project file ``.code-harnessignore``. Pass ``include_all=True`` to skip
both (``.git`` exclusion in ``base_rg_arguments`` remains).
"""

from __future__ import annotations

from pathlib import Path

HARNESS_IGNORE_FILENAME = ".code-harnessignore"

# gitignore-style paths relative to the search cwd; applied as ``--glob !<pat>``.
DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    ".code-harness/**",
    "**/.code-harness/**",
    "node_modules/**",
    "**/node_modules/**",
    ".venv/**",
    "**/.venv/**",
    "venv/**",
    "**/venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    ".tox/**",
    "**/.tox/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    "*.egg-info/**",
    "**/*.egg-info/**",
    "*.err",
    "**/*.err",
)

OMITTED_BY_SOURCE_FIRST = (
    "(No matches in source-first view; ignored paths may still match. "
    "Pass include_all=true to search everything.)"
)


def ignore_glob_flags(root: Path, *, include_all: bool) -> list[str]:
    """Return interleaved ``--glob`` / pattern flags, or empty when include_all."""
    if include_all:
        return []
    flags: list[str] = []
    for pattern in _exclusion_patterns(root):
        flags.extend(["--glob", f"!{pattern}"])
    return flags


def _exclusion_patterns(root: Path) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in (*DEFAULT_IGNORE_GLOBS, *_read_project_ignores(root)):
        normalized = pattern.strip().lstrip("!")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _read_project_ignores(root: Path) -> list[str]:
    path = root / HARNESS_IGNORE_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    patterns: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns
