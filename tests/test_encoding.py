"""Encoding: UTF-8 primary, windows-1252 fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.encoding import (
    FALLBACK_ENCODING,
    decode_bytes,
    decode_file,
    write_text,
)
from code_harness.paths import PathGuard
from code_harness.tools import grep, read, str_replace, write
from tests.conftest import requires_ripgrep

# "configuracao" with c-cedilla and a-tilde: configuracao
CP1252_WORD = "configura\u00e7\u00e3o"
CP1252_BYTES = CP1252_WORD.encode("cp1252")
REPLACEMENT = "altera\u00e7\u00e3o"


@pytest.fixture
def cp1252_file(project: Path) -> Path:
    target = project / "legado.txt"
    target.write_bytes(b"linha1: " + CP1252_BYTES + b"\r\nlinha2: ok\r\n")
    return target


def test_decode_utf8() -> None:
    decoded = decode_bytes("caf\u00e9".encode("utf-8"))
    assert decoded.text == "caf\u00e9"
    assert decoded.encoding == "utf-8"


def test_decode_utf8_bom() -> None:
    decoded = decode_bytes(b"\xef\xbb\xbfhello")
    assert decoded.text == "hello"
    assert decoded.encoding == "utf-8"


def test_decode_cp1252() -> None:
    decoded = decode_bytes(CP1252_BYTES)
    assert decoded.text == CP1252_WORD
    assert decoded.encoding == FALLBACK_ENCODING


def test_read_cp1252_file(guard: PathGuard, cp1252_file: Path) -> None:
    result = read(guard, path="legado.txt")
    assert isinstance(result, str)
    assert CP1252_WORD in result
    assert "\ufffd" not in result


def test_str_replace_preserves_cp1252(guard: PathGuard, cp1252_file: Path) -> None:
    message = str_replace(
        guard,
        path="legado.txt",
        old_string=CP1252_WORD,
        new_string=REPLACEMENT,
    )
    assert "Replaced 1 occurrence" in message
    raw = cp1252_file.read_bytes()
    assert REPLACEMENT.encode("cp1252") in raw
    assert CP1252_BYTES not in raw
    assert decode_file(cp1252_file).encoding == FALLBACK_ENCODING


def test_write_preserves_existing_cp1252(guard: PathGuard, cp1252_file: Path) -> None:
    write(guard, path="legado.txt", contents="nova " + CP1252_WORD + "\n")
    raw = cp1252_file.read_bytes()
    assert raw == ("nova " + CP1252_WORD + "\n").encode("cp1252")


def test_write_new_file_is_utf8(guard: PathGuard, project: Path) -> None:
    write(guard, path="novo.txt", contents="caf\u00e9\n")
    assert (project / "novo.txt").read_bytes() == "caf\u00e9\n".encode("utf-8")


def test_write_text_helper_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    write_text(path, CP1252_WORD, encoding="cp1252")
    assert path.read_bytes() == CP1252_BYTES


@requires_ripgrep
def test_grep_finds_cp1252_content(guard: PathGuard, cp1252_file: Path) -> None:
    result = grep(guard, pattern="configura", path="legado.txt")
    assert "No matches found" not in result
    assert CP1252_WORD in result
    assert "legado.txt:1:" in result


@requires_ripgrep
def test_grep_decodes_json_bytes_field() -> None:
    """Simulate ripgrep's base64 ``bytes`` payload for non-UTF-8 lines."""
    import base64

    from code_harness.tools.grep import _parse_line_entry

    payload = {
        "path": {"text": "legado.txt"},
        "line_number": 1,
        "lines": {"bytes": base64.b64encode(CP1252_BYTES + b"\n").decode("ascii")},
    }
    entry = _parse_line_entry(payload, is_match=True)
    assert entry is not None
    assert entry.text == CP1252_WORD
