from code_harness.application.dto.requests import (
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchFilesRequest,
    SearchTextRequest,
)
from code_harness.application.tools import (
    ListFilesTool,
    ReadFileTool,
    ReadRangeTool,
    SearchFilesTool,
    SearchTextTool,
)
from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.search_hit import SearchHit, SearchOutcome
from code_harness.domain.models.source_file import SourceFile


class FakeCatalog:
    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
    ) -> tuple[SourceFile, ...]:
        return (
            SourceFile("src/AgendaService.java", 10, 1, "java"),
            SourceFile("docs/agenda.md", 20, 2, "markdown"),
            SourceFile("src/Other.java", 30, 3, "java"),
        )


class FakeReader:
    def _result(self, path: str, start: int, end: int) -> SourceRead:
        return SourceRead(CodeSnippet(CodeLocation(path, start, end), "content", "text", "hash"))

    def read_file(self, path: str, *, max_chars: int, max_lines: int) -> SourceRead:
        return self._result(path, 1, 1)

    def read_range(
        self, path: str, *, start_line: int, end_line: int, max_chars: int
    ) -> SourceRead:
        return self._result(path, start_line, end_line)


class FakeSearcher:
    def search(self, **kwargs: object) -> SearchOutcome:
        snippet = CodeSnippet(CodeLocation("a.py", 1, 1), "needle", "python", "hash")
        return SearchOutcome(
            (SearchHit(snippet, 1.0, MatchType.EXACT, (str(kwargs["query"]),)),),
            warnings=("warning",),
        )


def test_list_and_search_file_tools_rank_and_truncate() -> None:
    listed = ListFilesTool(FakeCatalog()).execute(ListFilesRequest(max_results=2))
    searched = SearchFilesTool(FakeCatalog()).execute(
        SearchFilesRequest("AgendaService", max_results=1)
    )

    assert len(listed.data) == 2
    assert listed.truncated
    assert searched.data[0].source_file.path == "src/AgendaService.java"
    assert searched.data[0].score == 0.95


def test_search_text_tool_preserves_outcome_metadata() -> None:
    result = SearchTextTool(FakeSearcher()).execute(SearchTextRequest("needle"))

    assert result.data[0].matched_terms == ("needle",)
    assert result.warnings == ("warning",)


def test_read_tools_return_typed_snippets() -> None:
    file_result = ReadFileTool(FakeReader()).execute(ReadFileRequest("a.txt"))
    range_result = ReadRangeTool(FakeReader()).execute(ReadRangeRequest("a.txt", 2, 4))

    assert file_result.data.content == "content"
    assert range_result.data.location.start_line == 2
    assert range_result.data.location.end_line == 4
