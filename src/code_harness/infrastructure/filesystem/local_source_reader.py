from hashlib import sha256

from code_harness.domain.errors import (
    BinaryFileError,
    InvalidQueryError,
    ResultLimitExceededError,
    UnsupportedEncodingError,
)
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead
from code_harness.domain.models.code_location import CodeLocation
from code_harness.infrastructure.filesystem.encoding_detector import appears_binary, decode_source
from code_harness.infrastructure.filesystem.language_detection import detect_language
from code_harness.infrastructure.filesystem.path_guard import PathGuard


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
    ) -> SourceRead:
        text, file_hash, relative = self._load(path)
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[:max_lines])
        truncated = len(lines) > max_lines
        if len(selected) > max_chars:
            selected = selected[:max_chars]
            truncated = True
        end_line = 1 if not lines else min(len(lines), max_lines, selected.count("\n") + 1)
        warnings = ("Source content was truncated by configured read limits.",) if truncated else ()
        return SourceRead(
            CodeSnippet(
                CodeLocation(relative, 1, end_line),
                selected,
                detect_language(relative),
                file_hash,
            ),
            truncated=truncated,
            warnings=warnings,
        )

    def read_range(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
        max_chars: int,
    ) -> SourceRead:
        text, file_hash, relative = self._load(path)
        lines = text.splitlines(keepends=True)
        line_count = max(1, len(lines))
        if start_line > line_count:
            raise InvalidQueryError(
                "start_line exceeds the current file length.",
                path=relative,
                start_line=start_line,
                line_count=line_count,
            )
        actual_end = min(end_line, line_count)
        selected = "" if not lines else "".join(lines[start_line - 1 : actual_end])
        truncated = len(selected) > max_chars
        if truncated:
            selected = selected[:max_chars]
        warnings = ("Source range was truncated by max_chars.",) if truncated else ()
        return SourceRead(
            CodeSnippet(
                CodeLocation(relative, start_line, actual_end),
                selected,
                detect_language(relative),
                file_hash,
            ),
            truncated=truncated,
            warnings=warnings,
        )
