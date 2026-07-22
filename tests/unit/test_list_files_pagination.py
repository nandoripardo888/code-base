from code_harness.application.dto.requests import ListFilesRequest
from code_harness.application.tools.list_files import ListFilesTool
from code_harness.domain.errors import CursorStaleError
from code_harness.domain.models.source_file import SourceFile
import pytest


class FakeCatalog:
    def list_files(self, *, include_globs=(), exclude_globs=()):
        return (
            SourceFile("a.py", 1, 10, "python"),
            SourceFile("b.py", 2, 20, "python"),
            SourceFile("c.py", 3, 30, "python"),
        )


def test_list_files_paginates_without_duplicates_or_skips() -> None:
    tool = ListFilesTool(FakeCatalog())  # type: ignore[arg-type]
    first = tool.execute(ListFilesRequest(max_results=2))
    assert [item.path for item in first.data.items] == ["a.py", "b.py"]
    assert first.data.next_cursor is not None

    second = tool.execute(ListFilesRequest(max_results=2, cursor=first.data.next_cursor))
    assert [item.path for item in second.data.items] == ["c.py"]
    assert second.data.next_cursor is None


def test_list_files_rejects_stale_cursor_after_sort_change() -> None:
    tool = ListFilesTool(FakeCatalog())  # type: ignore[arg-type]
    first = tool.execute(ListFilesRequest(max_results=1, sort="path"))
    with pytest.raises(CursorStaleError):
        tool.execute(
            ListFilesRequest(
                max_results=1,
                cursor=first.data.next_cursor,
                sort="size",
            )
        )
