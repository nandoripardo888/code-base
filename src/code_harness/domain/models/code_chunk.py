from dataclasses import dataclass

from code_harness.domain.models.code_location import CodeLocation


@dataclass(frozen=True, slots=True)
class CodeSnippet:
    location: CodeLocation
    content: str
    language: str | None
    file_hash: str


@dataclass(frozen=True, slots=True)
class SourceRead:
    snippet: CodeSnippet
    truncated: bool = False
    warnings: tuple[str, ...] = ()
