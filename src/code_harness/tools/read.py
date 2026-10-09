"""Read: return numbered file lines, or raw bytes for images."""

from __future__ import annotations

import codecs
from collections import deque
from collections.abc import Callable
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

MAX_TEXT_BYTES = 2_000_000
MAX_IMAGE_BYTES = 4 * 1024 * 1024

_LINE_NUMBER_WIDTH = 6
_READ_CHUNK_BYTES = 64 * 1024
_BINARY_SAMPLE_BYTES = 64 * 1024
_BINARY_MESSAGE = "Unsupported binary file; Read accepts text and recognized image files only."


@dataclass(frozen=True, slots=True)
class ImageResult:
    """An image payload the MCP layer forwards to the model as-is."""

    data: bytes
    mime_type: str
    path: str


@dataclass(frozen=True, slots=True)
class _Line:
    number: int
    data: bytes
    complete: bool = True


@dataclass(frozen=True, slots=True)
class _ScanResult:
    utf8: bool
    reached_eof: bool


class _TextProbe:
    """Classify a bounded byte sample and test UTF-8 incrementally."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self._utf8 = True
        self._sampled = 0
        self._suspicious = 0
        self._nul = False

    def feed(self, chunk: bytes) -> None:
        if b"\x00" in chunk:
            self._nul = True
        if self._sampled < _BINARY_SAMPLE_BYTES:
            sample = chunk[: _BINARY_SAMPLE_BYTES - self._sampled]
            self._sampled += len(sample)
            self._suspicious += sum(
                (byte < 32 and byte not in {8, 9, 10, 12, 13}) or byte == 127
                for byte in sample
            )
        if self._utf8:
            try:
                self._decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                self._utf8 = False

    def finish(self, *, reached_eof: bool) -> bool:
        if self._utf8 and reached_eof:
            try:
                self._decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                self._utf8 = False
        threshold = max(1, self._sampled // 10)
        if self._nul or (
            self._suspicious and (self._sampled < 32 or self._suspicious > threshold)
        ):
            raise InvalidArgumentError(_BINARY_MESSAGE)
        return self._utf8


class _PositiveCollector:
    def __init__(self, *, start: int, limit: int | None) -> None:
        self.start = start
        self.limit = limit
        self.line_number = 1
        self.lines: list[_Line] = []
        self.current = bytearray()
        self.current_exists = False
        self.remaining_bytes = MAX_TEXT_BYTES
        self.byte_truncated = False
        self.has_more_lines = False
        self._waiting_for_more = False

    def data(self, fragment: bytes) -> tuple[int, bool]:
        if not fragment:
            return 0, False
        self.current_exists = True
        if self._waiting_for_more:
            self.has_more_lines = True
            return 0, True
        if self.line_number < self.start:
            return len(fragment), False
        if self.remaining_bytes == 0:
            self._store_current(complete=False)
            self.byte_truncated = True
            return 0, True
        taken = min(len(fragment), self.remaining_bytes)
        self.current.extend(fragment[:taken])
        self.remaining_bytes -= taken
        if taken < len(fragment):
            self._store_current(complete=False)
            self.byte_truncated = True
            return taken, True
        return taken, False

    def end(self, delimiter_size: int) -> bool:
        if self._waiting_for_more:
            self.has_more_lines = True
            return True
        if self.line_number >= self.start:
            if delimiter_size > self.remaining_bytes:
                self._store_current(complete=False)
                self.byte_truncated = True
                return True
            self.remaining_bytes -= delimiter_size
            self._store_current()
            if self.limit is not None and len(self.lines) >= self.limit:
                self._waiting_for_more = True
        self.line_number += 1
        self.current.clear()
        self.current_exists = False
        return False

    def eof(self) -> None:
        if self.current_exists and not self._waiting_for_more and self.line_number >= self.start:
            self._store_current()

    @property
    def total_lines(self) -> int:
        return self.line_number if self.current_exists else self.line_number - 1

    def _store_current(self, *, complete: bool = True) -> None:
        if self.lines and self.lines[-1].number == self.line_number:
            if not complete and self.lines[-1].complete:
                previous = self.lines[-1]
                self.lines[-1] = _Line(previous.number, previous.data, complete=False)
            return
        self.lines.append(_Line(self.line_number, bytes(self.current), complete=complete))


class _TailCollector:
    def __init__(self, count: int) -> None:
        self.count = count
        self.line_number = 1
        self.lines: deque[_Line] = deque()
        self.current = bytearray()
        self.current_exists = False
        self.current_truncated = False
        self.stored_bytes = 0
        self.byte_truncated = False
        # Even empty rendered lines consume at least eight bytes (number, pipe, newline).
        self.max_records = max(1, MAX_TEXT_BYTES // (_LINE_NUMBER_WIDTH + 2))

    def data(self, fragment: bytes) -> tuple[int, bool]:
        if not fragment:
            return 0, False
        self.current_exists = True
        available = MAX_TEXT_BYTES - len(self.current)
        if available > 0:
            self.current.extend(fragment[:available])
        if len(fragment) > available:
            self.current_truncated = True
        return len(fragment), False

    def end(self, _delimiter_size: int) -> bool:
        self._store_current()
        self.line_number += 1
        self.current.clear()
        self.current_exists = False
        self.current_truncated = False
        return False

    def eof(self) -> None:
        if self.current_exists:
            self._store_current()

    def _store_current(self) -> None:
        record = _Line(
            self.line_number,
            bytes(self.current),
            complete=not self.current_truncated,
        )
        self.lines.append(record)
        self.stored_bytes += len(record.data)
        while len(self.lines) > self.count:
            self.stored_bytes -= len(self.lines.popleft().data)
        while self.lines and (
            self.stored_bytes > MAX_TEXT_BYTES or len(self.lines) > self.max_records
        ):
            self.stored_bytes -= len(self.lines.popleft().data)
            self.byte_truncated = True


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
        return _read_image(resolved, mime_type=mime_type, relative=relative)

    if limit is not None and limit <= 0:
        raise InvalidArgumentError("limit must be greater than zero.")
    if offset == 0:
        raise InvalidArgumentError("offset is 1-indexed; use a positive or negative value.")

    size = resolved.stat().st_size
    if size <= MAX_TEXT_BYTES:
        with resolved.open("rb") as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
        if len(raw) <= MAX_TEXT_BYTES:
            return _read_small_text(raw, offset=offset, limit=limit)
    if offset is not None and offset < 0:
        return _read_large_tail(resolved, count=-offset, limit=limit)
    return _read_large_range(resolved, offset=offset, limit=limit)


def _read_image(path: Path, *, mime_type: str, relative: str) -> ImageResult:
    observed_size = path.stat().st_size
    if observed_size > MAX_IMAGE_BYTES:
        raise InvalidArgumentError(
            f"Image is too large: observed {observed_size} bytes; "
            f"maximum is {MAX_IMAGE_BYTES} bytes."
        )
    # Bound the read as well as the stat check so a concurrently growing file cannot
    # cause an unbounded allocation between the two operations.
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidArgumentError(
            f"Image is too large: observed at least {len(data)} bytes; "
            f"maximum is {MAX_IMAGE_BYTES} bytes."
        )
    return ImageResult(data, mime_type, relative)


def _read_small_text(raw: bytes, *, offset: int | None, limit: int | None) -> str:
    _raise_if_binary(raw)
    try:
        text = decode_bytes(raw).text
    except UnicodeDecodeError as error:
        raise InvalidArgumentError(_BINARY_MESSAGE) from error
    if not text:
        return "File is empty."

    lines = text.splitlines()
    start = _start_index(offset, len(lines))
    selected = lines[start:] if limit is None else lines[start : start + limit]
    if not selected:
        return f"No lines in range (file has {len(lines)} lines)."

    rendered = _render_text_lines(
        [(number, line) for number, line in enumerate(selected, start=start + 1)]
    )
    remaining = len(lines) - (start + len(selected))
    if remaining > 0:
        rendered += f"\n\n({remaining} more lines; pass offset={start + len(selected) + 1})"
    return rendered


def _read_large_range(path: Path, *, offset: int | None, limit: int | None) -> str:
    collector = _PositiveCollector(start=offset or 1, limit=limit)
    scan = _scan_file(path, on_data=collector.data, on_end=collector.end)
    if scan.reached_eof:
        collector.eof()

    if not collector.lines:
        return f"No lines in range (file has {collector.total_lines} lines)."

    rendered = _render_lines(collector.lines, utf8=scan.utf8)
    if collector.byte_truncated or any(not record.complete for record in collector.lines):
        return rendered + _byte_truncation_note()
    if collector.has_more_lines:
        next_offset = collector.lines[-1].number + 1
        return rendered + f"\n\n(More lines available; pass offset={next_offset})"
    return rendered


def _read_large_tail(path: Path, *, count: int, limit: int | None) -> str:
    collector = _TailCollector(count)
    scan = _scan_file(path, on_data=collector.data, on_end=collector.end)
    collector.eof()
    records = list(collector.lines)
    if limit is not None:
        records = records[:limit]
    if not records:
        return f"No lines in range (file has {collector.line_number - 1} lines)."

    rendered = _render_lines(records, utf8=scan.utf8)
    if collector.byte_truncated or any(not record.complete for record in collector.lines):
        rendered += _byte_truncation_note()
    elif limit is not None and limit < len(collector.lines):
        remaining = len(collector.lines) - limit
        rendered += f"\n\n({remaining} more lines; pass offset={records[-1].number + 1})"
    return rendered


def _scan_file(
    path: Path,
    *,
    on_data: Callable[[bytes], tuple[int, bool]],
    on_end: Callable[[int], bool],
) -> _ScanResult:
    probe = _TextProbe()
    reached_eof = False
    pending_cr = False
    stopped = False

    with path.open("rb") as stream:
        while not stopped:
            chunk = stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                reached_eof = True
                break
            probe.feed(chunk)
            position = 0

            if pending_cr:
                delimiter_size = 2 if chunk.startswith(b"\n") else 1
                if delimiter_size == 2:
                    position = 1
                stopped = on_end(delimiter_size)
                pending_cr = False
                if stopped:
                    break

            while position < len(chunk):
                cr = chunk.find(b"\r", position)
                lf = chunk.find(b"\n", position)
                candidates = [index for index in (cr, lf) if index >= 0]
                delimiter = min(candidates) if candidates else -1
                end = len(chunk) if delimiter < 0 else delimiter
                if end > position:
                    consumed, stopped = on_data(chunk[position:end])
                    position += consumed
                    if stopped:
                        break
                if delimiter < 0:
                    break
                if chunk[delimiter] == 13 and delimiter == len(chunk) - 1:
                    pending_cr = True
                    position = len(chunk)
                    break
                delimiter_size = (
                    2
                    if chunk[delimiter] == 13
                    and delimiter + 1 < len(chunk)
                    and chunk[delimiter + 1] == 10
                    else 1
                )
                position = delimiter + delimiter_size
                stopped = on_end(delimiter_size)
                if stopped:
                    break

    if reached_eof and pending_cr:
        on_end(1)
    return _ScanResult(utf8=probe.finish(reached_eof=reached_eof), reached_eof=reached_eof)


def _render_lines(lines: list[_Line] | deque[_Line], *, utf8: bool) -> str:
    rendered: list[tuple[int, str]] = []
    for line in lines:
        try:
            if utf8:
                decoder = codecs.getincrementaldecoder(
                    "utf-8-sig" if line.number == 1 else "utf-8"
                )("strict")
                text = decoder.decode(line.data, final=line.complete)
            else:
                text = line.data.decode("cp1252")
        except UnicodeDecodeError as error:
            raise InvalidArgumentError(_BINARY_MESSAGE) from error
        rendered.append((line.number, text))
    return _render_text_lines(rendered)


def _render_text_lines(lines: list[tuple[int, str]]) -> str:
    return "\n".join(f"{number:>{_LINE_NUMBER_WIDTH}}|{line}" for number, line in lines)


def _byte_truncation_note() -> str:
    return (
        f"\n\n(Output truncated at the {MAX_TEXT_BYTES:,}-byte text limit; "
        "request a narrower range with offset and limit.)"
    )


def _raise_if_binary(raw: bytes) -> None:
    probe = _TextProbe()
    probe.feed(raw)
    probe.finish(reached_eof=True)


def _start_index(offset: int | None, total: int) -> int:
    if offset is None:
        return 0
    if offset < 0:
        return max(0, total + offset)
    return min(offset - 1, total)
