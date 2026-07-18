from dataclasses import dataclass

from code_harness.domain.models.source_file import SourceFile


@dataclass(frozen=True, slots=True)
class FileMatch:
    source_file: SourceFile
    score: float
    reason: str
