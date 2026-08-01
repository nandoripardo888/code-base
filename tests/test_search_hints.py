"""Unit tests for empty-result search hints."""

from __future__ import annotations

from code_harness.tools.search_hints import append_glob_hints, append_grep_hints


def test_grep_hints_are_capped_and_actionable() -> None:
    text = append_grep_hints(
        "No matches found.",
        pattern="FooBar",
        path="src",
        glob="*.py",
        file_type=None,
        case_insensitive=False,
        include_all=False,
    )
    assert text.startswith("No matches found.\n\nSuggestions:\n")
    assert text.count("\n- ") <= 4
    assert "case_insensitive=true" in text
    assert 'output_mode="symbols"' in text
    assert 'glob="*FooBar*"' in text
    assert "glob_pattern" not in text
    assert "Glob with" not in text


def test_grep_hints_skip_include_all_when_source_first_note_present() -> None:
    text = append_grep_hints(
        "No matches found.\n\n(source-first note)",
        pattern="x",
        path=None,
        glob=None,
        file_type=None,
        case_insensitive=True,
        include_all=False,
        source_first_omitted=True,
    )
    assert "Pass include_all=true" not in text


def test_glob_hints_for_literal_name() -> None:
    text = append_glob_hints(
        "No files found matching 'Foo'.",
        glob_pattern="Foo",
        target_directory=None,
        include_all=False,
    )
    assert "*Foo*" in text
    assert "include_all=true" in text
