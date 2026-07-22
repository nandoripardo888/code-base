from hashlib import sha256

from code_harness.domain.errors import (
    BinaryFileError,
    InvalidQueryError,
    ResultLimitExceededError,
    UnsupportedEncodingError,
)
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead, TruncationInfo
from code_harness.domain.models.code_location import CodeLocation
from code_harness.infrastructure.filesystem.encoding_detector import appears_binary, decode_source
from code_harness.infrastructure.filesystem.language_detection import detect_language
from code_harness.infrastructure.filesystem.path_guard import PathGuard


def _truncate_at_line_boundary(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    last_newline = cut.rfind("\n")
    if last_newline >= 0:
        return cut[: last_newline + 1], True
    return cut, True


class LocalSourceReader:
    def __init__(self, guard: PathGuard, *, max_file_size_bytes: int = 2_000_000) -> None:
        self._guard = guard
        self._max_file_size_bytes = max_file_size_bytes

    def _load(self, path: str) -> tuple[str, str, str]:
        resolved, relative = self._guard.resolve_file(path)
        size = resolved.stat().st_size
        if size > self._max_file_size_bytes:
            raise ResultLimitExceededError(relative, self._max_file_size_bytes)
        data = resolved.read_bytes()
        if appears_binary(data):
            raise BinaryFileError(relative)
        decoded = decode_source(data)
        if decoded is None:
            raise UnsupportedEncodingError(relative)
        return decoded.text, sha256(data).hexdigest(), relative

    def read_file(
        self,
        path: str,
        *,
        max_chars: int,
        max_lines: int,
        include_line_numbers: bool = False,
    ) -> SourceRead:
        text, file_hash, relative = self._load(path)
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        selected_lines = lines[:max_lines]
        reason: str | None = None
        truncated = False
        if len(lines) > max_lines:
            truncated = True
            reason = "max_lines"
        selected = "".join(selected_lines)
        if len(selected) > max_chars:
            selected, char_truncated = _truncate_at_line_boundary(selected, max_chars)
            if char_truncated:
                truncated = True
                reason = "max_chars"
        end_line = 1 if not selected else selected.count("\n") + (
            0 if selected.endswith("\n") else 1
        )
        end_line = max(1, min(end_line, total_lines or 1))
        next_start = end_line + 1 if truncated and end_line < total_lines else None
        warnings = ("Source content was truncated by configured read limits.",) if truncated else ()
        numbered = None
        if include_line_numbers:
            numbered = tuple(
                (index, line.rstrip("\r\n"))
                for index, line in enumerate(selected.splitlines(keepends=True), start=1)
            )
        return SourceRead(
            CodeSnippet(
                CodeLocation(relative, 1, end_line),
                selected,
                detect_language(relative),
                file_hash,
            ),
            truncated=truncated,
            warnings=warnings,
            truncation=TruncationInfo(
                truncated=truncated,
                reason=reason,
                next_start_line=next_start,
                total_lines=total_lines,
            ),
            total_lines=total_lines,
            numbered_lines=numbered,
        )

    def read_range(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
        max_chars: int,
        include_line_numbers: bool = False,
    ) -> SourceRead:
        text, file_hash, relative = self._load(path)
        lines = text.splitlines(keepends=True)
        line_count = max(1, len(lines)) if lines else 1
        total_lines = len(lines)
        if start_line > line_count:
            raise InvalidQueryError(
                "start_line exceeds the current file length.",
                path=relative,
                start_line=start_line,
                line_count=line_count,
            )
        actual_end = min(end_line, line_count)
        selected = "" if not lines else "".join(lines[start_line - 1 : actual_end])
        reason: str | None = None
        truncated = False
        if len(selected) > max_chars:
            selected, truncated = _truncate_at_line_boundary(selected, max_chars)
            reason = "max_chars"
            if truncated:
                actual_end = start_line + selected.count("\n") - (
                    1 if selected.endswith("\n") else 0
                )
                actual_end = max(start_line, actual_end)
        next_start = actual_end + 1 if truncated and actual_end < end_line else None
        warnings = ("Source range was truncated by max_chars.",) if truncated else ()
        numbered = None
        if include_line_numbers:
            numbered = tuple(
                (start_line + index, line.rstrip("\r\n"))
                for index, line in enumerate(selected.splitlines(keepends=True))
            )
        return SourceRead(
            CodeSnippet(
                CodeLocation(relative, start_line, actual_end),
                selected,
                detect_language(relative),
                file_hash,
            ),
            truncated=truncated,
            warnings=warnings,
            truncation=TruncationInfo(
                truncated=truncated,
                reason=reason,
                next_start_line=next_start,
                total_lines=total_lines,
            ),
            requested_range=(start_line, end_line),
            actual_range=(start_line, actual_end),
            total_lines=total_lines,
            numbered_lines=numbered,
        )
