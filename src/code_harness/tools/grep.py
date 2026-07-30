"""Grep: regex search over the project, powered by ripgrep."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Literal

from code_harness import ripgrep
from code_harness.encoding import decode_bytes
from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard

OutputMode = Literal["content", "files_with_matches", "count"]

OUTPUT_MODES: tuple[OutputMode, ...] = ("content", "files_with_matches", "count")

# Ripgrep can emit millions of lines; keep responses bounded even without head_limit.
MATCH_CAP = 1_000


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str
    number: int
    text: str
    is_match: bool


def grep(
    guard: PathGuard,
    *,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    file_type: str | None = None,
    output_mode: str = "content",
    case_insensitive: bool = False,
    context_after: int | None = None,
    context_before: int | None = None,
    context_lines: int | None = None,
    multiline: bool = False,
    head_limit: int | None = None,
    offset: int | None = None,
) -> str:
    if output_mode not in OUTPUT_MODES:
        raise InvalidArgumentError(
            f"output_mode must be one of: {', '.join(OUTPUT_MODES)}; got {output_mode!r}."
        )
    if head_limit is not None and head_limit <= 0:
        raise InvalidArgumentError("head_limit must be greater than zero.")
    if offset is not None and offset < 0:
        raise InvalidArgumentError("offset must not be negative.")

    target = guard.resolve(path or ".")
    relative_target = guard.relative(target)

    arguments = ["--hidden", "--glob", "!.git/"]
    if case_insensitive:
        arguments.append("--ignore-case")
    if multiline:
        arguments.extend(["--multiline", "--multiline-dotall"])
    if glob:
        arguments.extend(["--glob", glob])
    if file_type:
        arguments.extend(["--type", file_type])

    if output_mode == "files_with_matches":
        arguments.append("--files-with-matches")
    elif output_mode == "count":
        # Ripgrep omits the path when a single file is searched.
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
    output = ripgrep.run(arguments, cwd=guard.root)

    if output_mode == "content":
        has_context = any(
            value is not None for value in (context_lines, context_before, context_after)
        )
        return _render_content(
            output,
            head_limit=head_limit,
            offset=offset or 0,
            separate_groups=has_context,
        )
    return _render_lines(output, head_limit=head_limit, offset=offset or 0)


def _render_lines(output: str, *, head_limit: int | None, offset: int) -> str:
    entries = [_normalize(line) for line in output.splitlines() if line.strip()]
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


def _render_content(
    output: str,
    *,
    head_limit: int | None,
    offset: int,
    separate_groups: bool,
) -> str:
    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    lines: list[str] = []
    pending: list[_Entry] = []
    matched = 0
    emitted = 0
    inside_window = False
    last: tuple[str, int] | None = None
    total_matches = 0

    for raw in output.splitlines():
        event = _parse_event(raw)
        if event is None:
            continue
        kind, data = event

        if kind == "summary":
            total_matches = _summary_matches(data)
            continue
        if kind == "begin":
            pending.clear()
            inside_window = False
            continue
        if kind not in ("match", "context"):
            continue

        entry = _parse_line_entry(data, is_match=kind == "match")
        if entry is None:
            continue

        if kind == "context":
            if inside_window:
                last = _append(lines, entry, last, separate_groups)
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
            last = _append(lines, buffered, last, separate_groups)
        pending.clear()
        last = _append(lines, entry, last, separate_groups)
        emitted += 1
        inside_window = True

    if not lines:
        return "No matches found."

    rendered = "\n".join(lines)
    remaining = max(total_matches, matched) - (offset + emitted)
    if remaining > 0:
        rendered += f"\n\n({remaining} more matches; pass offset={offset + emitted})"
    return rendered


def _append(
    lines: list[str],
    entry: _Entry,
    last: tuple[str, int] | None,
    separate_groups: bool,
) -> tuple[str, int]:
    broke_run = last is not None and (last[0] != entry.path or entry.number != last[1] + 1)
    if separate_groups and broke_run:
        lines.append("--")
    separator = ":" if entry.is_match else "-"
    lines.append(f"{entry.path}{separator}{entry.number}{separator}{entry.text}")
    return entry.path, entry.number


def _parse_event(raw: str) -> tuple[str, dict[str, Any]] | None:
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


def _parse_line_entry(data: dict[str, Any], *, is_match: bool) -> _Entry | None:
    path = _decode_text_or_bytes(data.get("path"))
    text = _decode_text_or_bytes(data.get("lines"))
    number = data.get("line_number")
    if path is None or not isinstance(number, int):
        return None
    content = text.rstrip("\r\n") if text is not None else ""
    return _Entry(_normalize(path), number, content, is_match)


def _decode_text_or_bytes(field: object) -> str | None:
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


def _summary_matches(data: dict[str, Any]) -> int:
    stats = data.get("stats")
    if isinstance(stats, dict):
        matched = stats.get("matched_lines")
        if isinstance(matched, int):
            return matched
    return 0


def _normalize(text: str) -> str:
    return text.replace("\\", "/").removeprefix("./")
