from dataclasses import dataclass

from code_harness.domain.models.code_location import CodeLocation


@dataclass(frozen=True, slots=True)
class CodeSnippet:
    location: CodeLocation
    content: str
    language: str | None
    file_hash: str


@dataclass(frozen=True, slots=True)
class TruncationInfo:
    truncated: bool
    reason: str | None = None
    next_start_line: int | None = None
    total_lines: int | None = None


@dataclass(frozen=True, slots=True)
class SourceRead:
    snippet: CodeSnippet
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    truncation: TruncationInfo | None = None
    requested_range: tuple[int, int] | None = None
    actual_range: tuple[int, int] | None = None
    total_lines: int | None = None
    numbered_lines: tuple[tuple[int, str], ...] | None = None
