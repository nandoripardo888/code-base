"""Integration tests for Grep output_mode=symbols."""

from __future__ import annotations

import pytest

from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.symbols.models import Symbol
from code_harness.symbols.service import render_symbols
from code_harness.tools import grep
from tests.conftest import requires_ripgrep

pytestmark = requires_ripgrep


def test_grep_symbols_outlines_python_file(guard: PathGuard) -> None:
    result = grep(guard, pattern="", path="src/hello.py", output_mode="symbols")
    assert "src/hello.py" in result
    assert "function hello" in result


def test_grep_symbols_finds_by_name(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", output_mode="symbols")
    assert "function hello" in result
    assert "src/hello.py" in result


def test_grep_symbols_requires_pattern_for_project_scope(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError, match="non-empty pattern"):
        grep(guard, pattern="", output_mode="symbols")


def test_grep_symbols_empty_has_hints(guard: PathGuard) -> None:
    result = grep(guard, pattern="DefinitelyMissingSymbolXYZ", output_mode="symbols")
    assert result.startswith("No symbols found.")
    assert "Suggestions:" in result


def test_render_symbols_groups_by_file() -> None:
    text = render_symbols(
        [
            Symbol("A", "class", "a.py", 1, "python"),
            Symbol("b", "function", "a.py", 2, "python"),
            Symbol("C", "class", "b.py", 1, "python"),
        ]
    )
    assert text.startswith("3 symbols in 2 files")
    assert "a.py\n  1 class A\n  2 function b" in text
