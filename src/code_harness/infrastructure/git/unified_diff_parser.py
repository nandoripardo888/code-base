"""Parse unified diff text into structured changed files and hunks."""

from __future__ import annotations

import hashlib
import re

from code_harness.domain.enums import FileChangeKind
from code_harness.domain.models.change_set import ChangedFile, ChangedHunk

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<header>.*)$"
)


def parse_unified_diff(text: str) -> tuple[ChangedFile, ...]:
    if not text.strip():
        return ()
    files: list[ChangedFile] = []
    current_old: str | None = None
    current_new: str | None = None
    current_binary = False
    hunks: list[ChangedHunk] = []
    hunk_lines: list[str] = []
    hunk_meta: tuple[int, int, int, int, str] | None = None

    def flush_hunk() -> None:
        nonlocal hunk_meta, hunk_lines
        if hunk_meta is None:
            return
        old_start, old_count, new_start, new_count, header = hunk_meta
        body = tuple(hunk_lines)
        digest = hashlib.sha256(
            f"{old_start}:{old_count}:{new_start}:{new_count}:{header}\n".encode()
            + "\n".join(body).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        hunks.append(
            ChangedHunk(
                hunk_id=digest,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=header.strip(),
                lines=body,
            )
        )
        hunk_meta = None
        hunk_lines = []

    def flush_file() -> None:
        nonlocal current_old, current_new, current_binary, hunks
        flush_hunk()
        if current_old is None and current_new is None:
            return
        path = current_new or current_old or ""
        if path.startswith("b/"):
            path = path[2:]
        old_path = current_old[2:] if current_old and current_old.startswith("a/") else current_old
        kind = _classify(old_path, path if current_new else None, current_binary)
        files.append(
            ChangedFile(
                path=path if path != "/dev/null" else (old_path or path),
                kind=kind,
                old_path=old_path if old_path not in {None, "/dev/null", path} else None,
                binary=current_binary,
                hunks=tuple(hunks),
            )
        )
        current_old = None
        current_new = None
        current_binary = False
        hunks = []

    for raw_line in text.splitlines():
        if raw_line.startswith("diff --git "):
            flush_file()
            continue
        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            current_binary = True
            continue
        if raw_line.startswith("--- "):
            current_old = raw_line[4:].strip()
            continue
        if raw_line.startswith("+++ "):
            current_new = raw_line[4:].strip()
            continue
        match = _HUNK_RE.match(raw_line)
        if match:
            flush_hunk()
            hunk_meta = (
                int(match.group("old_start")),
                int(match.group("old_count") or "1"),
                int(match.group("new_start")),
                int(match.group("new_count") or "1"),
                match.group("header") or "",
            )
            continue
        if hunk_meta is not None and (
            raw_line[:1] in {" ", "+", "-", "\\"} or raw_line == ""
        ):
            hunk_lines.append(raw_line)
    flush_file()
    return tuple(files)


def _classify(old_path: str | None, new_path: str | None, binary: bool) -> FileChangeKind:
    if binary:
        return FileChangeKind.BINARY
    if old_path in {None, "/dev/null"}:
        return FileChangeKind.ADDED
    if new_path in {None, "/dev/null"}:
        return FileChangeKind.DELETED
    if old_path != new_path:
        return FileChangeKind.RENAMED
    return FileChangeKind.MODIFIED
