"""Unit tests for brace expansion (no ripgrep required)."""

from __future__ import annotations

import pytest

from code_harness.errors import InvalidArgumentError
from code_harness.tools.search_globs import (
    exclusion_glob_flags,
    expand_braces,
    normalize_glob_patterns,
)


def test_expand_braces_simple() -> None:
    assert expand_braces("*.{py,md}") == ["*.py", "*.md"]


def test_expand_braces_nested() -> None:
    assert expand_braces("src/{a,b/{c,d}}.py") == [
        "src/a.py",
        "src/b/c.py",
        "src/b/d.py",
    ]


def test_expand_braces_no_braces() -> None:
    assert expand_braces("*.py") == ["*.py"]


def test_expand_braces_rejects_unmatched() -> None:
    with pytest.raises(InvalidArgumentError, match="unmatched"):
        expand_braces("*.{py,md")
    with pytest.raises(InvalidArgumentError, match="unmatched"):
        expand_braces("*.py}")


def test_expand_braces_rejects_empty_alternative() -> None:
    with pytest.raises(InvalidArgumentError, match="empty alternative"):
        expand_braces("*.{py,}")


def test_normalize_glob_patterns_list_and_braces() -> None:
    assert normalize_glob_patterns(["*.{py,md}", "README*"]) == [
        "*.py",
        "*.md",
        "README*",
    ]


def test_exclusion_globs_reject_negated_input() -> None:
    with pytest.raises(InvalidArgumentError, match="must not start"):
        exclusion_glob_flags("!generated/**")
