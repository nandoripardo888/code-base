from __future__ import annotations

import re
from pathlib import Path

_REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?im)\b(authorization|proxy-authorization)\s*:\s*"
            r"(?:bearer|basic)?\s*[^\s\r\n]+"
        ),
        rf"\1: {_REDACTED}",
    ),
    (
        re.compile(
            r"(?i)(--?(?:password|passwd|token|secret|api[-_]?key|access[-_]?key)"
            r"(?:=|\s+))[^\s]+"
        ),
        rf"\1{_REDACTED}",
    ),
    (
        re.compile(
            r"(?im)\b(password|passwd|token|secret|api[-_]?key|access[-_]?key)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
        rf"\1={_REDACTED}",
    ),
    (
        re.compile(r"(?i)\b(https?://)([^/\s:@]+):([^@\s/]+)@"),
        rf"\1{_REDACTED}:{_REDACTED}@",
    ),
    (
        re.compile(r"(?i)([?&](?:token|key|signature|sig|password)=)[^&#\s]+"),
        rf"\1{_REDACTED}",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        _REDACTED,
    ),
    (
        re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"),
        _REDACTED,
    ),
    (
        re.compile(r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"),
        _REDACTED,
    ),
)


class SensitiveDataRedactor:
    def __init__(self, project_root: str | Path | None = None) -> None:
        self._project_root = (
            Path(project_root).resolve(strict=False) if project_root is not None else None
        )

    def redact(self, value: str | None) -> str | None:
        if value is None:
            return None
        redacted = value
        for pattern, replacement in _PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def summarize_command(
        self,
        executable: str,
        args: tuple[str, ...],
        *,
        cwd: str,
    ) -> str:
        rendered = " ".join((executable, *args))
        rendered_cwd = cwd
        if self._project_root is not None:
            try:
                relative = Path(cwd).resolve(strict=False).relative_to(self._project_root)
                rendered_cwd = relative.as_posix() or "."
            except ValueError:
                pass
        return self.redact(f"{rendered} (cwd={rendered_cwd})") or ""
