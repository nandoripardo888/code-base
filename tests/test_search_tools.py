from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.tools import glob, grep
from tests.conftest import requires_ripgrep

pytestmark = requires_ripgrep


def test_grep_content_lists_matches(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello")
    assert "src/hello.py:1:def hello():" in result


def test_grep_is_case_sensitive_by_default(guard: PathGuard) -> None:
    assert grep(guard, pattern="HELLO") == "No matches found."


def test_grep_case_insensitive(guard: PathGuard) -> None:
    assert "hello" in grep(guard, pattern="HELLO", case_insensitive=True)


def test_grep_files_with_matches(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", output_mode="files_with_matches")
    assert "src/hello.py" in result
    assert ":1:" not in result


def test_grep_count(guard: PathGuard) -> None:
    assert grep(guard, pattern="alpha", path="data/notes.txt", output_mode="count") == (
        "data/notes.txt:2"
    )


def test_grep_scoped_to_a_file(guard: PathGuard) -> None:
    result = grep(guard, pattern="alpha", path="data/notes.txt")
    assert "README.md" not in result
    assert result.count("data/notes.txt") == 2


def test_grep_glob_filter(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", glob="*.md")
    assert "README.md" in result
    assert "hello.py" not in result


def test_grep_head_limit_and_offset(guard: PathGuard) -> None:
    first = grep(guard, pattern="alpha", path="data/notes.txt", head_limit=1)
    assert "data/notes.txt:1:alpha" in first
    assert "(1 more matches; pass offset=1)" in first

    second = grep(guard, pattern="alpha", path="data/notes.txt", offset=1)
    assert "data/notes.txt:3:alpha" in second
    assert "data/notes.txt:1:alpha" not in second


def test_grep_context_lines(guard: PathGuard) -> None:
    result = grep(guard, pattern="beta", path="data/notes.txt", context_lines=1)
    assert "data/notes.txt-1-alpha" in result
    assert "data/notes.txt:2:beta" in result
    assert "data/notes.txt-3-alpha" in result


def test_grep_multiline(guard: PathGuard) -> None:
    result = grep(guard, pattern=r"def hello.*return", multiline=True, path="src/hello.py")
    assert "No matches found." not in result


def test_grep_rejects_unknown_output_mode(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        grep(guard, pattern="hello", output_mode="everything")


def test_grep_rejects_bad_paging(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        grep(guard, pattern="hello", head_limit=0)
    with pytest.raises(InvalidArgumentError):
        grep(guard, pattern="hello", offset=-1)


def test_glob_finds_python_files(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="*.py")
    assert "total 2 files" in result
    assert "- src/hello.py" in result
    assert "- src/util.py" in result


def test_glob_prefixes_recursive_pattern(guard: PathGuard) -> None:
    assert "- src/hello.py" in glob(guard, glob_pattern="**/*.py")


def test_glob_scoped_directory(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="*.txt", target_directory="data")
    assert "- data/notes.txt" in result


def test_glob_sorts_by_modification_time(guard: PathGuard, project: Path) -> None:
    import os
    import time

    recent = time.time()
    os.utime(project / "src" / "util.py", (recent, recent))
    os.utime(project / "src" / "hello.py", (recent - 500, recent - 500))
    listing = glob(guard, glob_pattern="*.py")
    assert listing.index("src/util.py") < listing.index("src/hello.py")


def test_glob_without_matches(guard: PathGuard) -> None:
    assert glob(guard, glob_pattern="*.nope") == "No files found matching '*.nope'."


def test_search_does_not_block_on_an_open_stdin(project: Path) -> None:
    """Ripgrep reads stdin when given no path, which would hang under MCP."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        f"""
        from code_harness.paths import PathGuard
        from code_harness.tools import glob, grep

        guard = PathGuard({str(project)!r})
        print(glob(guard, glob_pattern="*.py"))
        print(grep(guard, pattern="hello"))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "src/hello.py" in completed.stdout
