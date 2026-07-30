"""Shared text decoding, newline metadata, and atomic writes.

Legacy Windows source files (especially Brazilian Portuguese) are often
cp1252. Patch application also needs to preserve UTF-8 BOMs and the original
line-ending convention, so all file-editing tools use this module.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PRIMARY_ENCODING = "utf-8-sig"
FALLBACK_ENCODING = "cp1252"
DEFAULT_WRITE_ENCODING = "utf-8"
UTF8_BOM = b"\xef\xbb\xbf"

LineEnding = Literal["LF", "CRLF", "CR", "MIXED", "NONE"]


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    has_bom: bool
    line_ending: LineEnding


def detect_line_ending(text: str) -> LineEnding:
    """Classify the newline sequences used by *text*."""
    crlf = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    lf = without_crlf.count("\n")
    cr = without_crlf.count("\r")
    kinds = sum(value > 0 for value in (crlf, lf, cr))
    if kinds == 0:
        return "NONE"
    if kinds > 1:
        return "MIXED"
    if crlf:
        return "CRLF"
    if lf:
        return "LF"
    return "CR"


def decode_bytes(raw: bytes) -> DecodedText:
    """Decode bytes as UTF-8 (BOM-aware), falling back to windows-1252."""
    if not raw:
        return DecodedText("", DEFAULT_WRITE_ENCODING, False, "NONE")
    has_bom = raw.startswith(UTF8_BOM)
    try:
        text = raw.decode(PRIMARY_ENCODING)
        return DecodedText(text, "utf-8", has_bom, detect_line_ending(text))
    except UnicodeDecodeError:
        text = raw.decode(FALLBACK_ENCODING)
        return DecodedText(text, FALLBACK_ENCODING, False, detect_line_ending(text))


def decode_file(path: Path) -> DecodedText:
    return decode_bytes(path.read_bytes())


def normalize_newlines(text: str, newline: str = "\n") -> str:
    """Normalize CRLF, CR, and LF to *newline*."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def encode_text(text: str, encoding: str, *, has_bom: bool = False) -> bytes:
    """Encode text using the previously detected encoding and optional BOM."""
    target = DEFAULT_WRITE_ENCODING if encoding in {PRIMARY_ENCODING, "utf-8"} else encoding
    encoded = text.encode(target)
    if has_bom and target == "utf-8" and not encoded.startswith(UTF8_BOM):
        return UTF8_BOM + encoded
    return encoded


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace *path* atomically using a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_text(
    path: Path,
    text: str,
    *,
    encoding: str,
    has_bom: bool = False,
    atomic: bool = False,
) -> None:
    content = encode_text(text, encoding, has_bom=has_bom)
    if atomic:
        atomic_write_bytes(path, content)
    else:
        path.write_bytes(content)
