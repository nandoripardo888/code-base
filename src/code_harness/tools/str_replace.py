"""StrReplace: guarded string substitution with newline-tolerant matching."""

from __future__ import annotations

import hashlib

from code_harness.encoding import decode_file, normalize_newlines, write_text
from code_harness.errors import (
    AmbiguousReplacementError,
    InvalidArgumentError,
    StaleFileError,
    StringNotFoundError,
    UnexpectedOccurrencesError,
)
from code_harness.paths import PathGuard


def str_replace(
    guard: PathGuard,
    *,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    ignore_line_endings: bool = True,
    expected_occurrences: int | None = None,
    expected_sha256: str | None = None,
    dry_run: bool = False,
) -> str:
    if old_string == new_string:
        raise InvalidArgumentError("new_string must differ from old_string.")
    if not old_string:
        raise InvalidArgumentError("old_string must not be empty.")
    if expected_occurrences is not None and expected_occurrences < 0:
        raise InvalidArgumentError("expected_occurrences must be zero or greater.")

    resolved = guard.resolve(path, kind="file")
    relative = guard.relative(resolved)
    raw = resolved.read_bytes()
    if expected_sha256 is not None:
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash.lower() != expected_sha256.lower():
            raise StaleFileError(relative)
    decoded = decode_file(resolved)
    original = decoded.text

    spans = _exact_spans(original, old_string)
    newline_tolerant = False
    if not spans and ignore_line_endings:
        spans = _normalized_spans(original, old_string)
        newline_tolerant = bool(spans)

    occurrences = len(spans)
    if expected_occurrences is not None and occurrences != expected_occurrences:
        raise UnexpectedOccurrencesError(relative, expected_occurrences, occurrences)
    if occurrences == 0:
        raise StringNotFoundError(relative)
    if occurrences > 1 and not replace_all:
        raise AmbiguousReplacementError(relative, occurrences)

    selected = spans if replace_all else spans[:1]
    replacement = _adapt_newlines(new_string, decoded.line_ending, original, selected[0])
    updated = original
    for start, end in reversed(selected):
        updated = updated[:start] + replacement + updated[end:]

    if not dry_run:
        write_text(
            resolved,
            updated,
            encoding=decoded.encoding,
            has_bom=decoded.has_bom,
            atomic=True,
        )

    applied = len(selected)
    suffix = "occurrence" if applied == 1 else "occurrences"
    action = "Would replace" if dry_run else "Replaced"
    detail = " using newline-tolerant matching" if newline_tolerant else ""
    return f"{action} {applied} {suffix} in {relative}{detail}."


def _exact_spans(text: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return spans
        spans.append((index, index + len(needle)))
        start = index + len(needle)


def _normalized_spans(text: str, needle: str) -> list[tuple[int, int]]:
    normalized, boundaries = _normalize_with_boundaries(text)
    normalized_needle = normalize_newlines(needle)
    if not normalized_needle:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = normalized.find(normalized_needle, start)
        if index < 0:
            return spans
        end = index + len(normalized_needle)
        spans.append((boundaries[index], boundaries[end]))
        start = end


def _normalize_with_boundaries(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    boundaries = [0]
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            normalized.append("\n")
            index += 2
        elif text[index] == "\r":
            normalized.append("\n")
            index += 1
        else:
            normalized.append(text[index])
            index += 1
        boundaries.append(index)
    return "".join(normalized), boundaries


def _adapt_newlines(
    replacement: str,
    line_ending: str,
    original: str,
    span: tuple[int, int],
) -> str:
    if "\n" not in normalize_newlines(replacement):
        return replacement
    newline = {
        "CRLF": "\r\n",
        "CR": "\r",
        "LF": "\n",
    }.get(line_ending)
    if newline is None:
        matched = original[span[0] : span[1]]
        newline = "\r\n" if "\r\n" in matched else "\r" if "\r" in matched else "\n"
    return normalize_newlines(replacement, newline)
