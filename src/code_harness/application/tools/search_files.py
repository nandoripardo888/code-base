from pathlib import PurePosixPath

from code_harness.application.dto.requests import SearchFilesRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.models.file_match import FileMatch, FileMatchEvidence
from code_harness.domain.models.project import Project
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.file_catalog import FileCatalog
from code_harness.domain.protocols.repository_store import RepositoryStore


class SearchFilesTool:
    def __init__(
        self,
        catalog: FileCatalog,
        *,
        project: Project | None = None,
        store: RepositoryStore | None = None,
    ) -> None:
        self._catalog = catalog
        self._project = project
        self._store = store

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
                    evidence = FileMatchEvidence("filename", "exact", name_value)
                elif query == stem:
                    score, reason = 0.95, "Exact file-stem match."
                    evidence = FileMatchEvidence("filename", "exact", stem_value)
                elif name.startswith(query):
                    score, reason = 0.9, "File name starts with the query."
                    evidence = FileMatchEvidence("filename", "prefix", name_value)
                elif query in name:
                    score, reason = 0.85, "File name contains the query."
                    evidence = FileMatchEvidence("filename", "substring", name_value)
                elif query in path:
                    score, reason = 0.65, "Relative path contains the query."
                    evidence = FileMatchEvidence("path", "substring", source_file.path)
                else:
                    continue
                matches.append(FileMatch(source_file, score, reason, evidence))
            matches.sort(key=lambda item: (-item.score, item.source_file.path))
            return tuple(matches)

        matches, elapsed_ms = timed(search)
        truncated = len(matches) > request.max_results
        return ToolResult(
            matches[: request.max_results],
            elapsed_ms,
            truncated=truncated,
            index_state=resolve_index_state(self._store, self._project),
        )
