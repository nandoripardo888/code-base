"""Build aligned, side-by-side rows directly from byte-exact snapshots."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from code_harness.encoding import decode_bytes
from code_harness.review.models import SideBySideRow

_CONTEXT_LINES = 3


@dataclass(frozen=True, slots=True)
class BuiltDiff:
    rows: tuple[SideBySideRow, ...]
    additions: int
    deletions: int
    binary: bool


def build_side_by_side(
    before: bytes | None,
    after: bytes | None,
    *,
    collapse_context: bool = True,
) -> BuiltDiff:
    """Return aligned rows and statistics without consulting the live project."""
    before_bytes = before or b""
    after_bytes = after or b""
    if _looks_binary(before_bytes) or _looks_binary(after_bytes):
        return BuiltDiff((), 0, 0, True)

    old_lines = decode_bytes(before_bytes).text.splitlines()
    new_lines = decode_bytes(after_bytes).text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows: list[SideBySideRow] = []
    additions = 0
    deletions = 0

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            equal_rows = [
                SideBySideRow(
                    "context",
                    old_start + offset + 1,
                    old_lines[old_start + offset],
                    new_start + offset + 1,
                    new_lines[new_start + offset],
                )
                for offset in range(old_end - old_start)
            ]
            rows.extend(_collapse_equal_rows(equal_rows) if collapse_context else equal_rows)
            continue
        if tag == "delete":
            deletions += old_end - old_start
            rows.extend(
                SideBySideRow("deletion", line + 1, old_lines[line], None, None)
                for line in range(old_start, old_end)
            )
            continue
        if tag == "insert":
            additions += new_end - new_start
            rows.extend(
                SideBySideRow("addition", None, None, line + 1, new_lines[line])
                for line in range(new_start, new_end)
            )
            continue

        old_count = old_end - old_start
        new_count = new_end - new_start
        deletions += old_count
        additions += new_count
        for offset in range(max(old_count, new_count)):
            has_old = offset < old_count
            has_new = offset < new_count
            kind = "replacement" if has_old and has_new else ("deletion" if has_old else "addition")
            rows.append(
                SideBySideRow(
                    kind,
                    old_start + offset + 1 if has_old else None,
                    old_lines[old_start + offset] if has_old else None,
                    new_start + offset + 1 if has_new else None,
                    new_lines[new_start + offset] if has_new else None,
                )
            )
    return BuiltDiff(tuple(rows), additions, deletions, False)


def _collapse_equal_rows(rows: list[SideBySideRow]) -> list[SideBySideRow]:
    if len(rows) <= (_CONTEXT_LINES * 2) + 1:
        return rows
    hidden = rows[_CONTEXT_LINES:-_CONTEXT_LINES]
    first = hidden[0]
    return [
        *rows[:_CONTEXT_LINES],
        SideBySideRow(
            "collapsed",
            None,
            None,
            None,
            None,
            hidden_lines=len(hidden),
            old_start=first.old_line_number,
            new_start=first.new_line_number,
        ),
        *rows[-_CONTEXT_LINES:],
    ]


def _looks_binary(content: bytes) -> bool:
    if not content:
        return False
    sample = content[:8192]
    if b"\x00" in sample:
        return True
    control = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return control / len(sample) > 0.30
