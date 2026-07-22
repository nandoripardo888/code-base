from dataclasses import dataclass

from code_harness.domain.models.source_file import SourceFile


@dataclass(frozen=True, slots=True)
class FileMatchEvidence:
    field: str
    type: str
    matched_fragment: str


@dataclass(frozen=True, slots=True)
class FileMatch:
    source_file: SourceFile
    score: float
    reason: str
    match: FileMatchEvidence | None = None
