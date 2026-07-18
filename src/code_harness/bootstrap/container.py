from dataclasses import dataclass

from code_harness.application.tools import (
    ListFilesTool,
    ReadFileTool,
    ReadRangeTool,
    SearchFilesTool,
    SearchRegexTool,
    SearchTextTool,
)
from code_harness.bootstrap.settings import Settings
from code_harness.infrastructure.filesystem import LocalFileCatalog, LocalSourceReader, PathGuard
from code_harness.infrastructure.ripgrep import RipgrepSearcher


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    list_files: ListFilesTool
    search_files: SearchFilesTool
    search_text: SearchTextTool
    search_regex: SearchRegexTool
    read_file: ReadFileTool
    read_range: ReadRangeTool


def build_container(settings: Settings) -> ApplicationContainer:
    guard = PathGuard(settings.root)
    catalog = LocalFileCatalog(guard)
    reader = LocalSourceReader(guard, max_file_size_bytes=settings.max_file_size_bytes)
    searcher = RipgrepSearcher(
        guard.root,
        reader,
        executable=settings.ripgrep_executable,
        max_file_size_bytes=settings.max_file_size_bytes,
    )
    return ApplicationContainer(
        list_files=ListFilesTool(catalog),
        search_files=SearchFilesTool(catalog),
        search_text=SearchTextTool(searcher),
        search_regex=SearchRegexTool(searcher),
        read_file=ReadFileTool(reader),
        read_range=ReadRangeTool(reader),
    )
