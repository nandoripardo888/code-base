from dataclasses import dataclass

from code_harness.domain.models.source_file import SourceFile


@dataclass(frozen=True, slots=True)
class FileListingPage:
    items: tuple[SourceFile, ...]
    total_count: int | None
    next_cursor: str | None
    sort: str
    sort_direction: str
