from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    size_bytes: int
    modified_at_ns: int
    language: str | None
