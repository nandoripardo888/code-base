"""Select query-focused line windows inside a source range."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FLOW_TERMS = frozenset(
    {
        "commit",
        "rollback",
        "close",
        "finally",
        "return",
        "session",
        "catch",
        "except",
        "throw",
        "throws",
    }
)


@dataclass(frozen=True, slots=True)
class LineWindow:
    start_line: int
    end_line: int
    reason: str
    score: float


def query_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.casefold() for token in _TOKEN.findall(query) if len(token) >= 3))


def select_query_windows(
    *,
    content: str,
    start_line: int,
    end_line: int,
    query: str,
    max_windows: int = 3,
    window_radius: int = 4,
) -> tuple[LineWindow, ...]:
    lines = content.splitlines(keepends=True)
    total = len(lines)
    if total == 0:
        return ()
    start = max(1, min(start_line, total))
    end = max(start, min(end_line, total))
    terms = query_terms(query)
    windows: list[LineWindow] = [
        LineWindow(start, min(end, start + 6), "method_signature", 1.0),
    ]

    scored_lines: list[tuple[float, int]] = []
    for line_no in range(start, end + 1):
        text = lines[line_no - 1].casefold()
        score = 0.0
        for term in terms:
            if term in text:
                score += 1.0
                if term in _FLOW_TERMS:
                    score += 0.5
        if score > 0:
            scored_lines.append((score, line_no))

    scored_lines.sort(key=lambda item: (-item[0], item[1]))
    for score, line_no in scored_lines[: max_windows - 1]:
        window_start = max(start, line_no - window_radius)
        window_end = min(end, line_no + window_radius)
        windows.append(
            LineWindow(
                window_start,
                window_end,
                "query_match",
                score,
            )
        )

    if any(term in terms for term in _FLOW_TERMS):
        for line_no in range(end, start - 1, -1):
            text = lines[line_no - 1].casefold()
            if any(term in text for term in _FLOW_TERMS if term in terms):
                windows.append(
                    LineWindow(
                        max(start, line_no - window_radius),
                        min(end, line_no + 2),
                        "flow_termination",
                        0.8,
                    )
                )
                break

    return _merge_windows(windows, max_windows=max_windows)


def render_windows(content: str, windows: tuple[LineWindow, ...]) -> tuple[str, int, int]:
    lines = content.splitlines(keepends=True)
    if not windows:
        return "", 1, 1
    parts: list[str] = []
    for index, window in enumerate(windows):
        if index > 0:
            parts.append("\n...\n")
        chunk = "".join(lines[window.start_line - 1 : window.end_line])
        parts.append(chunk)
    return "".join(parts), windows[0].start_line, windows[-1].end_line


def _merge_windows(windows: list[LineWindow], *, max_windows: int) -> tuple[LineWindow, ...]:
    ordered = sorted(windows, key=lambda item: (item.start_line, item.end_line))
    merged: list[LineWindow] = []
    for window in ordered:
        if not merged:
            merged.append(window)
            continue
        previous = merged[-1]
        if window.start_line <= previous.end_line + 1:
            reason = previous.reason
            if previous.reason == "method_signature" or window.reason == "method_signature":
                reason = "method_signature_and_match"
            elif window.score > previous.score:
                reason = window.reason
            merged[-1] = LineWindow(
                previous.start_line,
                max(previous.end_line, window.end_line),
                reason,
                max(previous.score, window.score),
            )
        else:
            merged.append(window)
    return tuple(merged[:max_windows])
