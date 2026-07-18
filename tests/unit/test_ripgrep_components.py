import json

from code_harness.infrastructure.ripgrep.command_builder import RipgrepCommandBuilder
from code_harness.infrastructure.ripgrep.output_parser import parse_match, parse_output


def _event(path: str, line: int, start: int = 2, end: int = 5) -> str:
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "line_number": line,
                "submatches": [{"start": start, "end": end}],
            },
        }
    )


def test_command_builder_uses_argument_list_and_fixed_strings() -> None:
    command = RipgrepCommandBuilder("custom-rg").build(
        query="--dangerous value",
        regex=False,
        include_globs=("*.java",),
        exclude_globs=("generated/**",),
        case_sensitive=True,
        max_results=7,
    )

    assert command[0] == "custom-rg"
    assert "--fixed-strings" in command
    assert "--case-sensitive" in command
    assert "*.java" in command
    assert "!generated/**" in command
    assert command[-3:] == ["--", "--dangerous value", "."]


def test_command_builder_leaves_regex_enabled() -> None:
    command = RipgrepCommandBuilder().build(
        query="a.+b",
        regex=True,
        include_globs=(),
        exclude_globs=(),
        case_sensitive=False,
        max_results=10,
    )

    assert "--fixed-strings" not in command
    assert "--ignore-case" in command


def test_output_parser_normalizes_and_deduplicates_matches() -> None:
    output = "\n".join((_event("src\\file.py", 3), _event("src\\file.py", 3), _event("b.py", 4)))

    matches, truncated = parse_output(output, limit=1)

    assert matches[0].path == "src/file.py"
    assert matches[0].start_column == 3
    assert truncated


def test_output_parser_ignores_invalid_and_non_match_events() -> None:
    assert parse_match("not json") is None
    assert parse_match(json.dumps({"type": "begin", "data": {}})) is None
    assert parse_match(json.dumps({"type": "match", "data": {}})) is None
