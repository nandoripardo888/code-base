from types import SimpleNamespace

from code_harness.domain.enums import IndexState, MatchType
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import FtsCandidate, IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.search_hit import SearchHit, SearchOutcome
from code_harness.infrastructure.persistence.fts_searcher import IndexedTextSearcher


class _Store:
    def get_status(self, project: Project) -> object:
        return SimpleNamespace(state=IndexState.READY)

    def search_fts(
        self,
        project_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[FtsCandidate, ...]:
        return (FtsCandidate("src/one.py", -1.0),)


class _Reader:
    def load(self, path: str) -> IndexedSource:
        content = "unique_needle()\n"
        return IndexedSource(path, content, len(content), 1, "python", "utf-8", "hash")


class _Fallback:
    def search(self, **kwargs: object) -> SearchOutcome:
        snippet = CodeSnippet(
            CodeLocation("src/one.py", 1, 1),
            "unique_needle()\n",
            "python",
            "hash",
        )
        return SearchOutcome(
            (
                SearchHit(
                    snippet,
                    1.0,
                    MatchType.EXACT_LITERAL,
                    ("unique_needle",),
                ),
            )
        )


def test_duplicate_fts_and_ripgrep_hit_does_not_mark_truncation() -> None:
    searcher = IndexedTextSearcher(
        Project("project", "root"),
        _Store(),  # type: ignore[arg-type]
        _Reader(),  # type: ignore[arg-type]
        _Fallback(),  # type: ignore[arg-type]
    )

    result = searcher.search(
        query="unique_needle",
        regex=False,
        include_globs=(),
        exclude_globs=(),
        case_sensitive=False,
        max_results=5,
        context_lines=0,
        timeout_seconds=1.0,
    )

    assert len(result.hits) == 1
    assert result.truncated is False
    assert result.truncation is None
