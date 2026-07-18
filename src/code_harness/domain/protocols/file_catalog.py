from typing import Protocol

from code_harness.domain.models.source_file import SourceFile


class FileCatalog(Protocol):
    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
    ) -> tuple[SourceFile, ...]: ...
