from typing import Protocol

from code_harness.domain.models.code_chunk import SourceRead


class SourceReader(Protocol):
    def read_file(
        self,
        path: str,
        *,
        max_chars: int,
        max_lines: int,
        include_line_numbers: bool = False,
    ) -> SourceRead: ...

    def read_range(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
        max_chars: int,
        include_line_numbers: bool = False,
    ) -> SourceRead: ...
