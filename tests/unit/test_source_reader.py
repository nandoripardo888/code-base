from hashlib import sha256
from pathlib import Path

import pytest

from code_harness.domain.errors import BinaryFileError, InvalidQueryError, ResultLimitExceededError
from code_harness.infrastructure.filesystem.local_source_reader import LocalSourceReader
from code_harness.infrastructure.filesystem.path_guard import PathGuard


def test_reader_returns_current_range_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    data = b"one\ntwo\nthree\n"
    source.write_bytes(data)
    reader = LocalSourceReader(PathGuard(tmp_path))

    result = reader.read_range("sample.py", start_line=2, end_line=3, max_chars=100)

    assert result.snippet.content == "two\nthree\n"
    assert result.snippet.location.start_line == 2
    assert result.snippet.location.end_line == 3
    assert result.snippet.file_hash == sha256(data).hexdigest()
    assert result.snippet.language == "python"


def test_reader_truncates_by_lines_and_characters(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    reader = LocalSourceReader(PathGuard(tmp_path))

    by_lines = reader.read_file("sample.txt", max_chars=100, max_lines=1)
    by_chars = reader.read_file("sample.txt", max_chars=3, max_lines=10)

    assert by_lines.snippet.content == "alpha\n"
    assert by_lines.truncated
    assert by_chars.snippet.content == "alp"
    assert by_chars.truncated
    assert by_chars.warnings


def test_reader_supports_cp1252(tmp_path: Path) -> None:
    (tmp_path / "legacy.sql").write_bytes("select 'ação';\n".encode("cp1252"))

    result = LocalSourceReader(PathGuard(tmp_path)).read_file(
        "legacy.sql", max_chars=100, max_lines=10
    )

    assert "ação" in result.snippet.content


def test_reader_rejects_binary_large_and_invalid_range(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")
    (tmp_path / "short.txt").write_text("one\n", encoding="utf-8")
    reader = LocalSourceReader(PathGuard(tmp_path), max_file_size_bytes=10)

    with pytest.raises(BinaryFileError):
        LocalSourceReader(PathGuard(tmp_path)).read_file("binary.bin", max_chars=100, max_lines=10)
    with pytest.raises(ResultLimitExceededError):
        reader.read_file("large.txt", max_chars=100, max_lines=10)
    with pytest.raises(InvalidQueryError):
        reader.read_range("short.txt", start_line=2, end_line=3, max_chars=100)


def test_reader_handles_empty_file(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").touch()

    result = LocalSourceReader(PathGuard(tmp_path)).read_range(
        "empty.txt", start_line=1, end_line=3, max_chars=100
    )

    assert result.snippet.content == ""
    assert result.snippet.location.end_line == 1
