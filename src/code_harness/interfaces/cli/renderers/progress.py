from __future__ import annotations

import sys

import typer

from code_harness.application.indexing.progress import IndexProgressEvent, IndexProgressPhase


def _echo_err(value: str) -> None:
    encoding = sys.stderr.encoding or "utf-8"
    printable = value.encode(encoding, errors="backslashreplace").decode(encoding)
    typer.echo(printable, err=True)


class IndexProgressPrinter:
    """Render indexing progress on stderr without touching machine-readable stdout."""

    def __init__(self, *, stream_is_tty: bool | None = None) -> None:
        self._tty = sys.stderr.isatty() if stream_is_tty is None else stream_is_tty
        self._last_percent: int | None = None
        self._last_status_key: tuple[IndexProgressPhase, str] | None = None
        self._active_line = False

    def __call__(self, event: IndexProgressEvent) -> None:
        if (
            event.phase
            in (
                IndexProgressPhase.ANALYZING,
                IndexProgressPhase.COMMITTING,
            )
            and event.total > 0
        ):
            message = event.message or (
                "Analyzing" if event.phase is IndexProgressPhase.ANALYZING else "Writing index"
            )
            status_key = (event.phase, message)
            if status_key != self._last_status_key:
                self._last_status_key = status_key
                self._last_percent = None
            percent = event.percent or 0
            path = event.path or ""
            if event.phase is IndexProgressPhase.ANALYZING:
                line = f"Indexing: analyzing {event.current}/{event.total} ({percent}%) {path}"
            else:
                line = (
                    f"Indexing: {message.casefold()} "
                    f"{event.current}/{event.total} ({percent}%) {path}"
                )
            if self._tty:
                self._write_status(line)
                self._active_line = True
                return
            if percent != self._last_percent and (
                percent % 5 == 0 or event.current == 1 or event.current == event.total
            ):
                self._last_percent = percent
                _echo_err(line)
            return

        if self._active_line:
            _echo_err("")
            self._active_line = False

        if event.phase is IndexProgressPhase.INITIALIZING:
            _echo_err("Indexing: preparing index database...")
        elif event.phase is IndexProgressPhase.DISCOVERING:
            _echo_err("Indexing: discovering files...")
        elif event.phase is IndexProgressPhase.EMBEDDING:
            _echo_err("Indexing: preparing embeddings...")
        elif event.phase is IndexProgressPhase.COMMITTING:
            message = event.message or "Writing index to disk"
            _echo_err(f"Indexing: {message.casefold()}...")
        elif event.phase is IndexProgressPhase.COMPLETE:
            _echo_err("Indexing: complete.")

    def _write_status(self, line: str) -> None:
        # Carriage return keeps a single live status line in interactive terminals.
        encoding = sys.stderr.encoding or "utf-8"
        printable = line.encode(encoding, errors="backslashreplace").decode(encoding)
        sys.stderr.write(f"\r{printable[:120]:<120}")
        sys.stderr.flush()
