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
    assert "src/hello.py" in result
    assert "1:def hello():" in result
    assert "matches in" in result  # multi-file heading summary


def test_grep_is_case_sensitive_by_default(guard: PathGuard) -> None:
    result = grep(guard, pattern="HELLO")
    assert result.startswith("No matches found.")
    assert "case_insensitive=true" in result


def test_grep_case_insensitive(guard: PathGuard) -> None:
    assert "hello" in grep(guard, pattern="HELLO", case_insensitive=True)


def test_grep_files_with_matches(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", output_mode="files_with_matches")
    assert "src/hello.py" in result
    assert ":1:" not in result


def test_grep_count(guard: PathGuard) -> None:
    assert grep(guard, pattern="alpha", path="data/notes.txt", output_mode="count") == (
        "2 matches in 1 file\n\ndata/notes.txt:2"
    )


def test_grep_count_ranks_files_and_keeps_full_totals(guard: PathGuard) -> None:
    (guard.root / "src" / "other.py").write_text("alpha\n", encoding="utf-8")
    result = grep(guard, pattern="alpha", output_mode="count", head_limit=1)
    assert result.startswith("3 matches in 2 files\n\ndata/notes.txt:2")
    assert "(1 more files; pass offset=1)" in result


def test_grep_scoped_to_a_file(guard: PathGuard) -> None:
    result = grep(guard, pattern="alpha", path="data/notes.txt")
    assert "README.md" not in result
    assert result.count("data/notes.txt") == 1  # path heading once
    assert "1:alpha" in result
    assert "3:alpha" in result


def test_grep_glob_filter(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", glob="*.md")
    assert "README.md" in result
    assert "hello.py" not in result


def test_grep_explicit_exclude_always_applies(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", exclude="src/**", include_all=True)
    assert "README.md" in result
    assert "src/hello.py" not in result
    assert "src/util.py" not in result


def test_grep_head_limit_and_offset(guard: PathGuard) -> None:
    first = grep(guard, pattern="alpha", path="data/notes.txt", head_limit=1)
    assert "data/notes.txt" in first
    assert "1:alpha" in first
    assert "(1 more matches; pass offset=1)" in first

    second = grep(guard, pattern="alpha", path="data/notes.txt", offset=1)
    assert "data/notes.txt" in second
    assert "3:alpha" in second
    assert "1:alpha" not in second


def test_grep_context_lines(guard: PathGuard) -> None:
    result = grep(guard, pattern="beta", path="data/notes.txt", context_lines=1)
    assert "data/notes.txt" in result
    assert "1-alpha" in result
    assert "2:beta" in result
    assert "3-alpha" in result


def test_grep_content_groups_by_file(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello")
    assert result.startswith("3 matches in 3 files\n\n")
    assert "src/hello.py:1:" not in result
    assert "\nsrc/hello.py\n1:def hello():" in f"\n{result}"
    assert "\nsrc/util.py\n" in f"\n{result}"
    assert "\nREADME.md\n" in f"\n{result}"


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


def test_glob_explicit_exclude_supports_lists_and_braces(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="*.{py,md}", exclude=["src/**"])
    assert "README.md" in result
    assert "src/hello.py" not in result


def test_glob_sorts_by_modification_time(guard: PathGuard, project: Path) -> None:
    import os
    import time

    recent = time.time()
    os.utime(project / "src" / "util.py", (recent, recent))
    os.utime(project / "src" / "hello.py", (recent - 500, recent - 500))
    listing = glob(guard, glob_pattern="*.py")
    assert listing.index("src/util.py") < listing.index("src/hello.py")


def test_glob_caps_ripgrep_output(guard: PathGuard, monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness.tools import search_core

    seen_limit: int | None = None

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        max_stdout_lines: int | None = None,
        **_kwargs: object,
    ) -> str:
        nonlocal seen_limit
        seen_limit = max_stdout_lines
        assert "--files" in arguments
        assert cwd == guard.root
        return "\n".join(f"file-{index:04d}.txt" for index in range(search_core.GLOB_CAP + 1))

    monkeypatch.setattr(search_core.ripgrep, "run", fake_run)

    result = glob(guard, glob_pattern="*.txt", include_all=True)

    assert seen_limit == search_core.GLOB_CAP + 1
    assert f"showing {search_core.GLOB_CAP} files; more matches exist" in result
    assert f"Result capped at {search_core.GLOB_CAP} files." in result
    assert "file-0999.txt" in result
    assert "file-1000.txt" not in result


def test_glob_without_matches(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="*.nope")
    assert result.startswith("No files found matching '*.nope'.")
    assert "Suggestions:" in result


def test_empty_grep_suggests_glob_for_symbol_like_pattern(guard: PathGuard) -> None:
    result = grep(guard, pattern="TotallyMissingSymbol")
    assert result.startswith("No matches found.")
    assert 'output_mode="symbols"' in result
    assert 'glob="*TotallyMissingSymbol*"' in result
    assert "glob_pattern" not in result
    assert "include_all=true" in result


def test_empty_glob_suggests_broader_pattern(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="MissingName")
    assert result.startswith("No files found matching 'MissingName'.")
    assert "*MissingName*" in result


def test_source_first_skips_code_harness_err(project: Path, guard: PathGuard) -> None:
    sample = project / ".code-harness" / "cli-samples"
    sample.mkdir(parents=True)
    (sample / "noise.err").write_text("UNIQUE_HARNESS_NOISE_TOKEN\n", encoding="utf-8")

    default = grep(guard, pattern="UNIQUE_HARNESS_NOISE_TOKEN")
    assert "No matches found." in default
    assert "include_all=true" in default
    assert ".code-harness" not in default.split("\n\n")[0]

    full = grep(guard, pattern="UNIQUE_HARNESS_NOISE_TOKEN", include_all=True)
    assert "UNIQUE_HARNESS_NOISE_TOKEN" in full
    assert ".code-harness/cli-samples/noise.err" in full


def test_source_first_skips_err_in_glob(project: Path, guard: PathGuard) -> None:
    sample = project / ".code-harness" / "cli-samples"
    sample.mkdir(parents=True)
    (sample / "noise.err").write_text("x\n", encoding="utf-8")

    default = glob(guard, glob_pattern="*.err")
    assert "No files found matching '*.err'." in default
    assert "include_all=true" in default

    full = glob(guard, glob_pattern="*.err", include_all=True)
    assert ".code-harness/cli-samples/noise.err" in full


def test_code_harnessignore_excludes_custom_paths(project: Path, guard: PathGuard) -> None:
    (project / "vendor").mkdir()
    (project / "vendor" / "secret.txt").write_text("VENDOR_ONLY_TOKEN\n", encoding="utf-8")
    (project / ".code-harnessignore").write_text("vendor/**\n", encoding="utf-8")

    default = grep(guard, pattern="VENDOR_ONLY_TOKEN")
    assert "No matches found." in default
    assert "include_all=true" in default

    full = grep(guard, pattern="VENDOR_ONLY_TOKEN", include_all=True)
    assert "vendor/secret.txt" in full


def test_glob_brace_expansion(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern="*.{py,md}")
    assert "src/hello.py" in result
    assert "src/util.py" in result
    assert "README.md" in result
    assert "notes.txt" not in result


def test_glob_pattern_list(guard: PathGuard) -> None:
    result = glob(guard, glob_pattern=["*.py", "*.md"])
    assert "src/hello.py" in result
    assert "README.md" in result


def test_glob_rejects_malformed_braces(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError, match="malformed brace"):
        glob(guard, glob_pattern="*.{py,md")


def test_grep_glob_brace_filter(guard: PathGuard) -> None:
    result = grep(guard, pattern="hello", glob="*.{md,py}")
    assert "README.md" in result
    assert "src/hello.py" in result
    assert "notes.txt" not in result


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
