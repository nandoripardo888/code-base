"""Read: return numbered file lines, or raw bytes for images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_harness.encoding import decode_bytes
from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard

IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_LINE_NUMBER_WIDTH = 6
_MAX_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class ImageResult:
    """An image payload the MCP layer forwards to the model as-is."""

    data: bytes
    mime_type: str
    path: str


def read(
    guard: PathGuard,
    *,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str | ImageResult:
    resolved = guard.resolve(path, kind="file")
    relative = guard.relative(resolved)

    mime_type = IMAGE_MIME_TYPES.get(resolved.suffix.lower())
    if mime_type is not None:
        return ImageResult(resolved.read_bytes(), mime_type, relative)

    if limit is not None and limit <= 0:
        raise InvalidArgumentError("limit must be greater than zero.")
    if offset == 0:
        raise InvalidArgumentError("offset is 1-indexed; use a positive or negative value.")

    text = _decode(resolved)
    if not text:
        return "File is empty."

    lines = text.splitlines()
    start = _start_index(offset, len(lines))
    selected = lines[start:] if limit is None else lines[start : start + limit]
    if not selected:
        return f"No lines in range (file has {len(lines)} lines)."

    rendered = "\n".join(
        f"{number:>{_LINE_NUMBER_WIDTH}}|{line}"
        for number, line in enumerate(selected, start=start + 1)
    )
    remaining = len(lines) - (start + len(selected))
    if remaining > 0:
        rendered += f"\n\n({remaining} more lines; pass offset={start + len(selected) + 1})"
    return rendered


def _start_index(offset: int | None, total: int) -> int:
    if offset is None:
        return 0
    if offset < 0:
        return max(0, total + offset)
    return min(offset - 1, total)


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > _MAX_BYTES:
        raw = raw[:_MAX_BYTES]
    return decode_bytes(raw).text
