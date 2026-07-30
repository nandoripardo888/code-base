from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.errors import (
    AmbiguousReplacementError,
    InvalidArgumentError,
    StringNotFoundError,
)
from code_harness.paths import PathGuard
from code_harness.tools import ImageResult, delete, read, str_replace, write

# Smallest valid 1x1 PNG.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_read_numbers_lines(guard: PathGuard) -> None:
    result = read(guard, path="src/hello.py")
    assert result == "     1|def hello():\n     2|    return 'world'"


def test_read_offset_and_limit(guard: PathGuard) -> None:
    assert read(guard, path="src/hello.py", offset=2, limit=1) == "     2|    return 'world'"


def test_read_negative_offset(guard: PathGuard) -> None:
    assert read(guard, path="src/hello.py", offset=-1) == "     2|    return 'world'"


def test_read_reports_remaining_lines(guard: PathGuard) -> None:
    result = read(guard, path="data/notes.txt", limit=1)
    assert isinstance(result, str)
    assert "(2 more lines; pass offset=2)" in result


def test_read_empty_file(guard: PathGuard, project: Path) -> None:
    (project / "empty.txt").write_text("", encoding="utf-8")
    assert read(guard, path="empty.txt") == "File is empty."


def test_read_offset_past_end(guard: PathGuard) -> None:
    result = read(guard, path="src/hello.py", offset=99)
    assert result == "No lines in range (file has 2 lines)."


def test_read_rejects_zero_offset(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        read(guard, path="src/hello.py", offset=0)


def test_read_rejects_non_positive_limit(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        read(guard, path="src/hello.py", limit=0)


def test_read_image(guard: PathGuard, project: Path) -> None:
    (project / "icon.png").write_bytes(PNG_BYTES)
    result = read(guard, path="icon.png")
    assert isinstance(result, ImageResult)
    assert result.mime_type == "image/png"
    assert result.data == PNG_BYTES


def test_write_creates_file(guard: PathGuard, project: Path) -> None:
    message = write(guard, path="new/deep.txt", contents="line1\nline2\n")
    assert message.startswith("Created new/deep.txt")
    assert (project / "new" / "deep.txt").read_text(encoding="utf-8") == "line1\nline2\n"


def test_write_overwrites_file(guard: PathGuard) -> None:
    write(guard, path="src/hello.py", contents="x\n")
    message = write(guard, path="src/hello.py", contents="y\n")
    assert message.startswith("Overwrote src/hello.py")


def test_write_preserves_newlines_verbatim(guard: PathGuard, project: Path) -> None:
    write(guard, path="crlf.txt", contents="a\r\nb\n")
    assert (project / "crlf.txt").read_bytes() == b"a\r\nb\n"


def test_str_replace_single_occurrence(guard: PathGuard, project: Path) -> None:
    message = str_replace(guard, path="src/hello.py", old_string="world", new_string="terra")
    assert message == "Replaced 1 occurrence in src/hello.py."
    assert "terra" in (project / "src" / "hello.py").read_text(encoding="utf-8")


def test_str_replace_rejects_ambiguity(guard: PathGuard) -> None:
    with pytest.raises(AmbiguousReplacementError, match="appears 2 times"):
        str_replace(guard, path="data/notes.txt", old_string="alpha", new_string="gamma")


def test_str_replace_all(guard: PathGuard, project: Path) -> None:
    message = str_replace(
        guard,
        path="data/notes.txt",
        old_string="alpha",
        new_string="gamma",
        replace_all=True,
    )
    assert message == "Replaced 2 occurrences in data/notes.txt."
    assert (project / "data" / "notes.txt").read_text(encoding="utf-8").count("gamma") == 2


def test_str_replace_missing_string(guard: PathGuard) -> None:
    with pytest.raises(StringNotFoundError):
        str_replace(guard, path="src/hello.py", old_string="absent", new_string="x")


def test_str_replace_requires_a_change(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        str_replace(guard, path="src/hello.py", old_string="world", new_string="world")


def test_str_replace_rejects_empty_old_string(guard: PathGuard) -> None:
    with pytest.raises(InvalidArgumentError):
        str_replace(guard, path="src/hello.py", old_string="", new_string="x")


def test_delete_removes_file(guard: PathGuard, project: Path) -> None:
    assert delete(guard, path="data/notes.txt") == "Deleted data/notes.txt."
    assert not (project / "data" / "notes.txt").exists()


def test_delete_missing_file_is_graceful(guard: PathGuard) -> None:
    message = delete(guard, path="ghost.txt")
    assert message.startswith("Could not delete ghost.txt")


def test_delete_directory_is_graceful(guard: PathGuard, project: Path) -> None:
    message = delete(guard, path="src")
    assert message.startswith("Could not delete src")
    assert (project / "src").is_dir()


def test_delete_outside_root_is_graceful(guard: PathGuard, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    message = delete(guard, path=str(outside))
    assert "outside" in message
    assert outside.exists()
