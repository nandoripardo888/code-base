from hashlib import sha256

from code_harness.domain.errors import (
    BinaryFileError,
    ResultLimitExceededError,
    UnsupportedEncodingError,
)
from code_harness.domain.models.index_report import IndexedSource
from code_harness.infrastructure.filesystem.encoding_detector import appears_binary, decode_source
from code_harness.infrastructure.filesystem.language_detection import detect_language
from code_harness.infrastructure.filesystem.path_guard import PathGuard


class LocalIndexSourceReader:
    def __init__(self, guard: PathGuard, *, max_file_size_bytes: int = 2_000_000) -> None:
        self._guard = guard
        self._max_file_size_bytes = max_file_size_bytes

    def load(self, path: str) -> IndexedSource:
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
        stat = resolved.stat()
        return IndexedSource(
            path=relative,
            content=decoded.text,
            size_bytes=len(data),
            modified_at_ns=stat.st_mtime_ns,
            language=detect_language(relative),
            encoding=decoded.encoding,
            content_hash=sha256(data).hexdigest(),
        )
