from typing import Protocol

from code_harness.domain.models.index_report import IndexedSource


class IndexSourceReader(Protocol):
    def load(self, path: str) -> IndexedSource: ...
