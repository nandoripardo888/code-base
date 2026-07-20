from types import SimpleNamespace

from code_harness.application.dto.requests import BuildContextRequest
from code_harness.application.tools.build_context import BuildContextTool
from code_harness.domain.enums import IndexState, MatchType
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import HybridSearchHit, SearchEvidence
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import (
    CodeReference,
    CodeSymbol,
    StructuralSearchResult,
)
from code_harness.domain.models.tool_result import ToolResult


def _source(path: str, content: str, content_hash: str = "hash") -> IndexedSource:
    return IndexedSource(path, content, len(content), 1, "python", "utf-8", content_hash)


class FakeSearchCode:
    def __init__(self, hits: tuple[HybridSearchHit, ...]) -> None:
        self.hits = hits

    def execute(self, request: object) -> ToolResult[tuple[HybridSearchHit, ...]]:
        return ToolResult(self.hits, 1, warnings=("search warning",))


class FakeReader:
    def __init__(self, sources: dict[str, IndexedSource]) -> None:
        self.sources = sources

    def load(self, path: str) -> IndexedSource:
        return self.sources[path]


class FakeStore:
    def __init__(self, *, ready: bool = True, stale_reference: bool = False) -> None:
        self.ready = ready
        self.stale_reference = stale_reference
        self.child = CodeSymbol(
            "child",
            "work",
            "Module.work",
            "function",
            CodeLocation("src/module.py", 2, 3),
            parent_symbol_id="parent",
        )
        self.parent = CodeSymbol(
            "parent",
            "Module",
            "Module",
            "class",
            CodeLocation("src/module.py", 1, 4),
        )

    def get_status(self, project: Project) -> object:
        return SimpleNamespace(
            state=IndexState.READY if self.ready else IndexState.NOT_INITIALIZED,
            structural_schema_ready=self.ready,
        )

    def find_symbols_by_ids(
        self, project_id: str, symbol_ids: tuple[str, ...]
    ) -> tuple[StructuralSearchResult, ...]:
        results = []
        if "child" in symbol_ids:
            results.append(StructuralSearchResult(self.child, None, "", "hash"))
        if "parent" in symbol_ids:
            results.append(StructuralSearchResult(self.parent, None, "", "hash"))
        return tuple(results)

    def find_references(
        self, project_id: str, target_name: str, *, limit: int
    ) -> tuple[StructuralSearchResult, ...]:
        if target_name != "work":
            return ()
        reference = CodeReference(
            "ref",
            "work",
            "call",
            CodeLocation("src/caller.py", 1, 1),
        )
        return (
            StructuralSearchResult(
                None,
                reference,
                "",
                "stale" if self.stale_reference else "hash",
            ),
        )


def _hybrid_hit(content: str = "def work():\n    return 1\n") -> HybridSearchHit:
    end_line = max(3, len(content.splitlines()) + 1)
    snippet = CodeSnippet(
        CodeLocation("src/module.py", 2, end_line),
        content,
        "python",
        "hash",
    )
    evidence = SearchEvidence(MatchType.SYMBOL, 1, 1.0, 1.0, 0.1, "child", "work")
    return HybridSearchHit(snippet, 1.0, (evidence,), ("work",), "Matching symbol definition.")


def test_context_builder_expands_references_and_deduplicates_parent() -> None:
    tool = BuildContextTool(
        FakeSearchCode((_hybrid_hit(),)),  # type: ignore[arg-type]
        Project("project", "root"),
        FakeStore(),  # type: ignore[arg-type]
        FakeReader(
            {
                "src/module.py": _source(
                    "src/module.py",
                    "class Module:\n    def work():\n        return 1\n\n",
                ),
                "src/caller.py": _source("src/caller.py", "work()\n"),
            }
        ),
    )

    result = tool.execute(BuildContextRequest("work", max_tokens=1_000))

    assert {item.role for item in result.data.snippets} == {"definition", "reference"}
    assert "search warning" in result.warnings
    assert result.data.omitted_results >= 1


def test_context_builder_reports_unavailable_or_stale_expansion() -> None:
    sources = {
        "src/module.py": _source("src/module.py", "class Module:\n    pass\n"),
        "src/caller.py": _source("src/caller.py", "work()\n"),
    }
    no_index = BuildContextTool(
        FakeSearchCode((_hybrid_hit(),)),  # type: ignore[arg-type]
        Project("project", "root"),
        FakeStore(ready=False),  # type: ignore[arg-type]
        FakeReader(sources),
    ).execute(BuildContextRequest("work"))
    stale = BuildContextTool(
        FakeSearchCode((_hybrid_hit(),)),  # type: ignore[arg-type]
        Project("project", "root"),
        FakeStore(stale_reference=True),  # type: ignore[arg-type]
        FakeReader(sources),
    ).execute(BuildContextRequest("work"))

    assert any("not ready" in warning for warning in no_index.warnings)
    assert any("stale context expansion" in warning for warning in stale.warnings)


def test_context_builder_clips_complete_lines_to_budget() -> None:
    long_content = "".join(f"line {number} with content\n" for number in range(20))
    hit = _hybrid_hit(long_content)
    result = BuildContextTool(
        FakeSearchCode((hit,)),  # type: ignore[arg-type]
        Project("project", "root"),
        FakeStore(),  # type: ignore[arg-type]
        FakeReader({"src/module.py": _source("src/module.py", long_content)}),
    ).execute(
        BuildContextRequest(
            "work",
            max_tokens=50,
            max_snippets=1,
            max_expansion_depth=0,
        )
    )

    assert result.data.estimated_tokens <= 50
    assert result.data.snippets[0].truncated
    assert result.data.snippets[0].snippet.content.endswith("\n")


def test_context_builder_revalidates_seed_immediately_before_selection() -> None:
    result = BuildContextTool(
        FakeSearchCode((_hybrid_hit(),)),  # type: ignore[arg-type]
        Project("project", "root"),
        FakeStore(),  # type: ignore[arg-type]
        FakeReader(
            {
                "src/module.py": _source(
                    "src/module.py",
                    "def work():\n    return 2\n",
                    "changed",
                )
            }
        ),
    ).execute(BuildContextRequest("work", max_expansion_depth=0))

    assert result.data.snippets == ()
    assert any("stale context snippet" in warning for warning in result.warnings)
