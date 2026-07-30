"""Write: create or overwrite a file with the given contents."""

from __future__ import annotations

from code_harness.encoding import DEFAULT_WRITE_ENCODING, decode_file, write_text
from code_harness.paths import PathGuard


def write(guard: PathGuard, *, path: str, contents: str) -> str:
    resolved = guard.resolve(path, kind="file", must_exist=False)
    existed = resolved.exists()
    decoded = decode_file(resolved) if existed else None
    encoding = decoded.encoding if decoded else DEFAULT_WRITE_ENCODING
    has_bom = decoded.has_bom if decoded else False
    resolved.parent.mkdir(parents=True, exist_ok=True)
    write_text(
        resolved,
        contents,
        encoding=encoding,
        has_bom=has_bom,
        atomic=True,
    )

    action = "Overwrote" if existed else "Created"
    line_count = len(contents.splitlines())
    return f"{action} {guard.relative(resolved)} ({line_count} lines, {len(contents)} chars)."
