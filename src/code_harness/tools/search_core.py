"""Shared search internals for Grep and Glob.

Not exposed as an MCP tool. Future phases (ignores, format, braces, hints,
symbols) extend this module; ``grep`` / ``glob`` remain the public surface.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from code_harness import ripgrep
from code_harness.encoding import decode_bytes
from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.tools.search_globs import (
    GlobInput,
    exclusion_glob_flags,
    format_glob_label,
    normalize_glob_patterns,
    with_recursive_prefix,
)
from code_harness.tools.search_hints import append_glob_hints, append_grep_hints
from code_harness.tools.search_ignores import OMITTED_BY_SOURCE_FIRST, ignore_glob_flags

OutputMode = Literal["content", "files_with_matches", "count", "symbols", "references"]

OUTPUT_MODES: tuple[OutputMode, ...] = (
    "content",
    "files_with_matches",
    "count",
    "symbols",
    "references",
)

# Ripgrep can emit millions of lines; keep responses bounded even without head_limit.
MATCH_CAP = 1_000


@dataclass(frozen=True, slots=True)
class MatchEntry:
    path: str
    number: int
    text: str
    is_match: bool


def base_rg_arguments(
    root: Path,
    *,
    include_all: bool = False,
    exclude: GlobInput | None = None,
) -> list[str]:
    """Flags shared by every project search (content or files)."""
    return [
        "--hidden",
        "--glob",
        "!.git/",
        *ignore_glob_flags(root, include_all=include_all),
        *exclusion_glob_flags(exclude),
    ]


def normalize_path(text: str) -> str:
    return text.replace("\\", "/").removeprefix("./")


def validate_paging(*, head_limit: int | None, offset: int | None) -> None:
    if head_limit is not None and head_limit <= 0:
        raise InvalidArgumentError("head_limit must be greater than zero.")
    if offset is not None and offset < 0:
        raise InvalidArgumentError("offset must not be negative.")


def grep_content(
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
    if output_mode not in OUTPUT_MODES:
        raise InvalidArgumentError(
            f"output_mode must be one of: {', '.join(OUTPUT_MODES)}; got {output_mode!r}."
        )
    if output_mode != "references" and (
        reference_kind is not None or exclude_reference_kind is not None
    ):
        raise InvalidArgumentError(
            "reference_kind filters are only valid with output_mode=references."
        )
    validate_paging(head_limit=head_limit, offset=offset)

    if pattern is None:
        target = guard.resolve(path or ".", kind="any")
        if output_mode == "symbols" and target.is_file():
            pattern = ""
        else:
            raise InvalidArgumentError(
                "pattern is required except for output_mode=symbols with path set to a file."
            )
    elif not pattern and output_mode != "symbols":
        raise InvalidArgumentError(
            "pattern must not be empty except for output_mode=symbols with path set to a file."
        )

    if output_mode == "symbols":
        from code_harness.symbols.service import grep_symbols

        return grep_symbols(
            guard,
            pattern=pattern,
            path=path,
            glob=glob,
            file_type=file_type,
            case_insensitive=case_insensitive,
            head_limit=head_limit,
            offset=offset,
            include_all=include_all,
            exclude=exclude,
        )

    if output_mode == "references":
        from code_harness.symbols.references import find_references

        return find_references(
            guard,
            pattern=pattern,
            path=path,
            glob=glob,
            file_type=file_type,
            case_insensitive=case_insensitive,
            head_limit=head_limit,
            offset=offset or 0,
            include_all=include_all,
            exclude=exclude,
            reference_kind=reference_kind,
            exclude_reference_kind=exclude_reference_kind,
        )

    glob_filters = None if glob is None else normalize_glob_patterns(glob)

    target = guard.resolve(path or ".")
    relative_target = guard.relative(target)

    arguments = _grep_rg_arguments(
        guard.root,
        pattern=pattern,
        relative_target=relative_target,
        glob_filters=glob_filters,
        file_type=file_type,
        output_mode=output_mode,
        case_insensitive=case_insensitive,
        context_after=context_after,
        context_before=context_before,
        context_lines=context_lines,
        multiline=multiline,
        include_all=include_all,
        exclude=exclude,
    )
    output = ripgrep.run(arguments, cwd=guard.root)

    if output_mode == "content":
        has_context = any(
            value is not None for value in (context_lines, context_before, context_after)
        )
        rendered = render_content(
            output,
            head_limit=head_limit,
            offset=offset or 0,
            separate_groups=has_context,
        )
    elif output_mode == "count":
        rendered = render_count(output, head_limit=head_limit, offset=offset or 0)
    else:
        rendered = render_lines(output, head_limit=head_limit, offset=offset or 0)

    if rendered != "No matches found.":
        return rendered

    source_first_omitted = False
    if not include_all and _grep_has_matches_without_harness_ignores(
        guard,
        pattern=pattern,
        relative_target=relative_target,
        glob_filters=glob_filters,
        file_type=file_type,
        case_insensitive=case_insensitive,
        multiline=multiline,
        exclude=exclude,
    ):
        rendered = f"{rendered}\n\n{OMITTED_BY_SOURCE_FIRST}"
        source_first_omitted = True

    return append_grep_hints(
        rendered,
        pattern=pattern,
        path=path,
        glob=glob,
        file_type=file_type,
        case_insensitive=case_insensitive,
        include_all=include_all,
        source_first_omitted=source_first_omitted,
    )


def list_files(
    guard: PathGuard,
    *,
    glob_pattern: GlobInput,
    target_directory: str | None = None,
    include_all: bool = False,
    exclude: GlobInput | None = None,
) -> str:
    label = format_glob_label(glob_pattern)
    patterns = [with_recursive_prefix(item) for item in normalize_glob_patterns(glob_pattern)]
    directory = guard.resolve(target_directory or ".", kind="directory")

    arguments = [
        *base_rg_arguments(guard.root, include_all=include_all, exclude=exclude),
        "--files",
    ]
    for pattern in patterns:
        arguments.extend(["--glob", pattern])
    arguments.extend(exclusion_glob_flags(exclude))
    arguments.extend(["--", "."])
    output = ripgrep.run(arguments, cwd=directory)
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    if paths:
        # Dedupe while preserving ripgrep order before mtime sort.
        absolute: list[Path] = []
        seen: set[str] = set()
        for entry in paths:
            key = normalize_path(entry)
            if key in seen:
                continue
            seen.add(key)
            absolute.append(directory / entry)
        absolute.sort(key=_modified_at, reverse=True)

        listing = "\n".join(f"- {guard.relative(entry)}" for entry in absolute)
        location = guard.relative(directory)
        return f"Result of search in '{location}' (total {len(absolute)} files):\n{listing}"

    message = f"No files found matching '{label}'."
    source_first_omitted = False
    if not include_all and _glob_has_files_without_harness_ignores(
        guard.root, directory=directory, patterns=patterns, exclude=exclude
    ):
        message = f"{message}\n\n{OMITTED_BY_SOURCE_FIRST}"
        source_first_omitted = True
    return append_glob_hints(
        message,
        glob_pattern=glob_pattern,
        target_directory=target_directory,
        include_all=include_all,
        source_first_omitted=source_first_omitted,
    )


def _grep_rg_arguments(
    root: Path,
    *,
    pattern: str,
    relative_target: str,
    glob_filters: list[str] | None,
    file_type: str | None,
    output_mode: str,
    case_insensitive: bool,
    context_after: int | None,
    context_before: int | None,
    context_lines: int | None,
    multiline: bool,
    include_all: bool,
    exclude: GlobInput | None,
) -> list[str]:
    arguments = base_rg_arguments(root, include_all=include_all, exclude=exclude)
    if case_insensitive:
        arguments.append("--ignore-case")
    if multiline:
        arguments.extend(["--multiline", "--multiline-dotall"])
    if glob_filters:
        for item in glob_filters:
            arguments.extend(["--glob", item])
    if file_type:
        arguments.extend(["--type", file_type])
    # Ripgrep resolves overlapping glob rules by the last matching rule.
    # Repeat explicit exclusions after positive filters so callers cannot
    # accidentally re-include paths they explicitly excluded.
    arguments.extend(exclusion_glob_flags(exclude))

    if output_mode == "files_with_matches":
        arguments.append("--files-with-matches")
    elif output_mode == "count":
        arguments.extend(["--count", "--with-filename"])
    else:
        arguments.extend(["--json", "--line-number"])
        if context_lines is not None:
            arguments.extend(["--context", str(context_lines)])
        else:
            if context_before is not None:
                arguments.extend(["--before-context", str(context_before)])
            if context_after is not None:
                arguments.extend(["--after-context", str(context_after)])

    arguments.extend(["--regexp", pattern, "--", relative_target])
    return arguments


def _grep_has_matches_without_harness_ignores(
    guard: PathGuard,
    *,
    pattern: str,
    relative_target: str,
    glob_filters: list[str] | None,
    file_type: str | None,
    case_insensitive: bool,
    multiline: bool,
    exclude: GlobInput | None,
) -> bool:
    arguments = _grep_rg_arguments(
        guard.root,
        pattern=pattern,
        relative_target=relative_target,
        glob_filters=glob_filters,
        file_type=file_type,
        output_mode="files_with_matches",
        case_insensitive=case_insensitive,
        context_after=None,
        context_before=None,
        context_lines=None,
        multiline=multiline,
        include_all=True,
        exclude=exclude,
    )
    output = ripgrep.run(arguments, cwd=guard.root)
    return bool(output.strip())


def _glob_has_files_without_harness_ignores(
    root: Path,
    *,
    directory: Path,
    patterns: list[str],
    exclude: GlobInput | None,
) -> bool:
    arguments = [*base_rg_arguments(root, include_all=True, exclude=exclude), "--files"]
    for pattern in patterns:
        arguments.extend(["--glob", pattern])
    arguments.extend(exclusion_glob_flags(exclude))
    arguments.extend(["--", "."])
    output = ripgrep.run(arguments, cwd=directory)
    return bool(output.strip())


def render_lines(output: str, *, head_limit: int | None, offset: int) -> str:
    entries = [normalize_path(line) for line in output.splitlines() if line.strip()]
    if not entries:
        return "No matches found."

    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    window = entries[offset : offset + limit]
    if not window:
        return f"No matches in range (total {len(entries)}, offset {offset})."

    rendered = "\n".join(window)
    remaining = len(entries) - (offset + len(window))
    if remaining > 0:
        rendered += f"\n\n({remaining} more; pass offset={offset + len(window)})"
    return rendered


def render_count(output: str, *, head_limit: int | None, offset: int) -> str:
    entries: list[tuple[str, int]] = []
    for raw in output.splitlines():
        path, separator, count_text = raw.rpartition(":")
        if not separator:
            continue
        try:
            count = int(count_text)
        except ValueError:
            continue
        entries.append((normalize_path(path), count))
    if not entries:
        return "No matches found."

    entries.sort(key=lambda item: (-item[1], item[0]))
    total_matches = sum(count for _, count in entries)
    total_files = len(entries)
    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    window = entries[offset : offset + limit]
    if not window:
        return f"No matches in range (total {total_files} files, offset {offset})."

    rendered = f"{_match_summary(total_matches, total_files)}\n\n"
    rendered += "\n".join(f"{path}:{count}" for path, count in window)
    remaining = total_files - (offset + len(window))
    if remaining > 0:
        rendered += f"\n\n({remaining} more files; pass offset={offset + len(window)})"
    return rendered


def render_content(
    output: str,
    *,
    head_limit: int | None,
    offset: int,
    separate_groups: bool,
) -> str:
    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    lines: list[str] = []
    pending: list[MatchEntry] = []
    matched = 0
    emitted = 0
    inside_window = False
    last: tuple[str, int] | None = None
    current_path: str | None = None
    files_seen: set[str] = set()
    total_matches = 0

    for raw in output.splitlines():
        event = parse_event(raw)
        if event is None:
            continue
        kind, data = event

        if kind == "summary":
            total_matches = summary_matches(data)
            continue
        if kind == "begin":
            pending.clear()
            inside_window = False
            continue
        if kind not in ("match", "context"):
            continue

        entry = parse_line_entry(data, is_match=kind == "match")
        if entry is None:
            continue

        if kind == "context":
            if inside_window:
                current_path, last = append_line(
                    lines, entry, last, separate_groups, current_path=current_path
                )
            else:
                pending.append(entry)
            continue

        index = matched
        matched += 1
        if index < offset or emitted >= limit:
            pending.clear()
            inside_window = False
            continue

        for buffered in pending:
            current_path, last = append_line(
                lines, buffered, last, separate_groups, current_path=current_path
            )
        pending.clear()
        current_path, last = append_line(
            lines, entry, last, separate_groups, current_path=current_path
        )
        files_seen.add(entry.path)
        emitted += 1
        inside_window = True

    if not lines:
        return "No matches found."

    body = "\n".join(lines)
    if len(files_seen) > 1:
        rendered = f"{_match_summary(emitted, len(files_seen))}\n\n{body}"
    else:
        rendered = body
    remaining = max(total_matches, matched) - (offset + emitted)
    if remaining > 0:
        rendered += f"\n\n({remaining} more matches; pass offset={offset + emitted})"
    return rendered


def append_line(
    lines: list[str],
    entry: MatchEntry,
    last: tuple[str, int] | None,
    separate_groups: bool,
    *,
    current_path: str | None,
) -> tuple[str | None, tuple[str, int]]:
    if current_path != entry.path:
        if lines:
            lines.append("")
        lines.append(entry.path)
    else:
        broke_run = last is not None and entry.number != last[1] + 1
        if separate_groups and broke_run:
            lines.append("--")
    separator = ":" if entry.is_match else "-"
    lines.append(f"{entry.number}{separator}{entry.text}")
    return entry.path, (entry.path, entry.number)


def _match_summary(matches: int, files: int) -> str:
    match_word = "match" if matches == 1 else "matches"
    file_word = "file" if files == 1 else "files"
    return f"{matches} {match_word} in {files} {file_word}"


def parse_event(raw: str) -> tuple[str, dict[str, Any]] | None:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    data = event.get("data")
    if not isinstance(kind, str) or not isinstance(data, dict):
        return None
    return kind, data


def parse_line_entry(data: dict[str, Any], *, is_match: bool) -> MatchEntry | None:
    path = decode_text_or_bytes(data.get("path"))
    text = decode_text_or_bytes(data.get("lines"))
    number = data.get("line_number")
    if path is None or not isinstance(number, int):
        return None
    content = text.rstrip("\r\n") if text is not None else ""
    return MatchEntry(normalize_path(path), number, content, is_match)


def decode_text_or_bytes(field: object) -> str | None:
    """Ripgrep emits ``text`` for UTF-8 and ``bytes`` (base64) otherwise."""
    if not isinstance(field, dict):
        return None
    text = field.get("text")
    if isinstance(text, str):
        return text
    encoded = field.get("bytes")
    if not isinstance(encoded, str):
        return None
    try:
        raw = base64.b64decode(encoded)
    except Exception:
        return None
    return decode_bytes(raw).text


def summary_matches(data: dict[str, Any]) -> int:
    stats = data.get("stats")
    if isinstance(stats, dict):
        matched = stats.get("matched_lines")
        if isinstance(matched, int):
            return matched
    return 0


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
