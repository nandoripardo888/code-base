from pathlib import PurePosixPath

from code_harness.application.dto.requests import SearchFilesRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.file_catalog import FileCatalog


class SearchFilesTool:
    def __init__(self, catalog: FileCatalog) -> None:
        self._catalog = catalog

    def execute(self, request: SearchFilesRequest) -> ToolResult[tuple[FileMatch, ...]]:
        def search() -> tuple[FileMatch, ...]:
            query = request.query if request.case_sensitive else request.query.casefold()
            matches: list[FileMatch] = []
            for source_file in self._catalog.list_files(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
            ):
                path = source_file.path if request.case_sensitive else source_file.path.casefold()
                name_value = PurePosixPath(source_file.path).name
                name = name_value if request.case_sensitive else name_value.casefold()
                stem_value = PurePosixPath(source_file.path).stem
                stem = stem_value if request.case_sensitive else stem_value.casefold()
                if query == name:
                    score, reason = 1.0, "Exact file-name match."
                elif query == stem:
                    score, reason = 0.95, "Exact file-stem match."
                elif query in name:
                    score, reason = 0.85, "File name contains the query."
                elif query in path:
                    score, reason = 0.65, "Relative path contains the query."
                else:
                    continue
                matches.append(FileMatch(source_file, score, reason))
            matches.sort(key=lambda item: (-item.score, item.source_file.path))
            return tuple(matches)

        matches, elapsed_ms = timed(search)
        truncated = len(matches) > request.max_results
        return ToolResult(matches[: request.max_results], elapsed_ms, truncated=truncated)
