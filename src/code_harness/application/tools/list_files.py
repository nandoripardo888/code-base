from code_harness.application.dto.requests import ListFilesRequest
from code_harness.application.pagination import (
    ListingCursor,
    assert_cursor_compatible,
    decode_cursor,
    encode_cursor,
    query_hash,
)
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.models.file_listing import FileListingPage
from code_harness.domain.models.project import Project
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.file_catalog import FileCatalog
from code_harness.domain.protocols.repository_store import RepositoryStore


def _sort_key(item: SourceFile, sort: str) -> tuple[object, str]:
    path = item.path.casefold()
    if sort == "size":
        return (item.size_bytes, path)
    if sort == "mtime":
        return (item.modified_at_ns, path)
    if sort == "language":
        return ((item.language or "").casefold(), path)
    return (path, path)


def _sort_value(item: SourceFile, sort: str) -> str:
    if sort == "size":
        return str(item.size_bytes)
    if sort == "mtime":
        return str(item.modified_at_ns)
    if sort == "language":
        return item.language or ""
    return item.path


class ListFilesTool:
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

    def execute(self, request: ListFilesRequest) -> ToolResult[FileListingPage]:
        def list_page() -> FileListingPage:
            files = self._catalog.list_files(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
            )
            revision = str(max((item.modified_at_ns for item in files), default=0))
            expected_hash = query_hash(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
                sort=request.sort,
                sort_direction=request.sort_direction,
            )
            ordered = sorted(
                files,
                key=lambda item: _sort_key(item, request.sort),
                reverse=request.sort_direction == "desc",
            )
            start_index = 0
            if request.cursor:
                cursor = decode_cursor(request.cursor)
                assert_cursor_compatible(
                    cursor,
                    index_revision=revision,
                    expected_query_hash=expected_hash,
                    sort_field=request.sort,
                    sort_direction=request.sort_direction,
                )
                if cursor.last_path is not None:
                    for index, item in enumerate(ordered):
                        if item.path.casefold() == cursor.last_path.casefold():
                            start_index = index + 1
                            break
            page_items = tuple(ordered[start_index : start_index + request.max_results])
            next_cursor = None
            if start_index + request.max_results < len(ordered) and page_items:
                last = page_items[-1]
                next_cursor = encode_cursor(
                    ListingCursor(
                        index_revision=revision,
                        query_hash=expected_hash,
                        sort_field=request.sort,
                        sort_direction=request.sort_direction,
                        last_sort_value=_sort_value(last, request.sort),
                        last_path=last.path,
                    )
                )
            return FileListingPage(
                page_items,
                len(ordered) if request.include_total_count else None,
                next_cursor,
                request.sort,
                request.sort_direction,
            )

        page, elapsed_ms = timed(list_page)
        return ToolResult(
            page,
            elapsed_ms,
            truncated=page.next_cursor is not None,
            index_state=resolve_index_state(self._store, self._project),
        )
