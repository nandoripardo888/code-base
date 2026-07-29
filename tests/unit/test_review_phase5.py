from typing import Any, cast

from code_harness.application.dto.review_requests import (
    BuildReviewContextRequest,
    FindChangeImpactsRequest,
    SuggestValidationPlanRequest,
)
from code_harness.application.review.build_review_context import BuildReviewContextTool
from code_harness.application.review.find_change_impacts import FindChangeImpactsTool
from code_harness.application.review.suggest_validation_plan import SuggestValidationPlanTool
from code_harness.domain.enums import FileChangeKind
from code_harness.domain.models.change_set import (
    ChangedFile,
    ChangedHunk,
    ChangeDiff,
    ChangedSymbol,
)
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.review import ChangeImpact, ImpactReference, ImpactTest
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import CodeReference, StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult


class _FakeChangedSymbols:
    def execute(self, request: Any) -> ToolResult[tuple[ChangedSymbol, ...]]:
        return ToolResult(
            (
                ChangedSymbol(
                    symbol_id="sym-1",
                    path="src/app.py",
                    kind="function",
                    name="main",
                    qualified_name="main",
                    start_line=1,
                    end_line=3,
                    hunk_ids=("h1",),
                    side="new",
                ),
            ),
            1,
        )


class _FakeReferences:
    def execute(self, request: Any) -> ToolResult[tuple[StructuralSearchResult, ...]]:
        return ToolResult(
            (
                StructuralSearchResult(
                    symbol=None,
                    reference=CodeReference(
                        reference_id="r1",
                        target_name="main",
                        kind="call",
                        location=CodeLocation(
                            path="src/other.py",
                            start_line=10,
                            end_line=10,
                        ),
                        confidence=0.9,
                    ),
                    content=None,
                    file_hash="h",
                ),
                StructuralSearchResult(
                    symbol=None,
                    reference=CodeReference(
                        reference_id="r2",
                        target_name="main",
                        kind="configuration_textual",
                        location=CodeLocation(
                            path="config/app.json",
                            start_line=2,
                            end_line=2,
                        ),
                        confidence=0.7,
                    ),
                    content=None,
                    file_hash="h",
                ),
            ),
            1,
        )


class _FakeSearchFiles:
    def execute(self, request: Any) -> ToolResult[tuple[FileMatch, ...]]:
        return ToolResult(
            (
                FileMatch(
                    source_file=SourceFile(
                        path="tests/unit/test_app.py",
                        size_bytes=10,
                        modified_at_ns=1,
                        language="python",
                    ),
                    score=1.0,
                    reason="name",
                ),
            ),
            1,
        )


class _FakeReadDiff:
    def execute(self, request: Any) -> ToolResult[ChangeDiff]:
        return ToolResult(
            ChangeDiff(
                change_set_id=request.change_set_id,
                files=(
                    ChangedFile(
                        path="src/app.py",
                        kind=FileChangeKind.MODIFIED,
                        hunks=(
                            ChangedHunk(
                                hunk_id="h1",
                                old_start=1,
                                old_count=2,
                                new_start=1,
                                new_count=3,
                                header="main",
                                lines=(" def main():", '+    return 2', "     pass"),
                            ),
                        ),
                    ),
                ),
                unified_text="diff",
            ),
            1,
        )


class _FakeImpacts:
    def execute(self, request: Any) -> ToolResult[tuple[ChangeImpact, ...]]:
        return ToolResult(
            (
                ChangeImpact(
                    symbol_id="sym-1",
                    name="main",
                    qualified_name="main",
                    path="src/app.py",
                    kind="function",
                    callers=(
                        ImpactReference(
                            path="src/other.py",
                            start_line=10,
                            end_line=10,
                            kind="call",
                        ),
                    ),
                    related_tests=(
                        ImpactTest(
                            path="tests/unit/test_app.py",
                            reason="name_match",
                            score=1.0,
                        ),
                    ),
                    config_files=(
                        ImpactReference(
                            path="config/app.json",
                            start_line=2,
                            end_line=2,
                            kind="configuration_textual",
                        ),
                    ),
                ),
            ),
            1,
        )


class _FakeListFiles:
    def execute(self, request: Any) -> ToolResult[tuple[ChangedFile, ...]]:
        return ToolResult(
            (
                ChangedFile(path="src/app.py", kind=FileChangeKind.MODIFIED),
                ChangedFile(path="tests/unit/test_app.py", kind=FileChangeKind.ADDED),
            ),
            1,
        )


class _FakeCatalog:
    def list_files(self, **_kwargs: object) -> tuple[SourceFile, ...]:
        return (
            SourceFile(
                path="pyproject.toml",
                size_bytes=1,
                modified_at_ns=1,
                language=None,
            ),
            SourceFile(
                path="tests/unit/test_app.py",
                size_bytes=1,
                modified_at_ns=1,
                language="python",
            ),
        )


class _FakeReader:
    def load(self, path: str) -> IndexedSource:
        content = {
            "src/app.py": "def main():\n    return 2\n    pass\n",
            "src/other.py": "def caller():\n    main()\n",
            "tests/unit/test_app.py": "def test_main():\n    assert True\n",
            "config/app.json": '{\n  "entry": "main"\n}\n',
        }.get(path, "")
        return IndexedSource(
            path=path,
            content=content,
            size_bytes=len(content),
            modified_at_ns=1,
            language="python",
            encoding="utf-8",
            content_hash="hash",
        )


def test_find_change_impacts_partitions_refs_and_tests() -> None:
    tool = FindChangeImpactsTool(
        changed_symbols=cast(Any, _FakeChangedSymbols()),
        find_references=cast(Any, _FakeReferences()),
        search_files=cast(Any, _FakeSearchFiles()),
    )
    result = tool.execute(FindChangeImpactsRequest(change_set_id="cs-1"))
    assert len(result.data) == 1
    impact = result.data[0]
    assert impact.callers[0].path == "src/other.py"
    assert impact.config_files[0].path == "config/app.json"
    assert impact.related_tests[0].path == "tests/unit/test_app.py"


def test_build_review_context_prioritizes_diff() -> None:
    tool = BuildReviewContextTool(
        reader=_FakeReader(),
        read_diff=cast(Any, _FakeReadDiff()),
        changed_symbols=cast(Any, _FakeChangedSymbols()),
        find_impacts=cast(Any, _FakeImpacts()),
    )
    result = tool.execute(
        BuildReviewContextRequest(change_set_id="cs-1", max_tokens=4_000, reserved_tokens=500)
    )
    roles = [item.role for item in result.data.snippets]
    assert "diff" in roles
    assert result.data.coverage["diff_files"] == 1
    assert "commands_not_executed" in result.data.limitations


def test_suggest_validation_plan_is_deterministic() -> None:
    tool = SuggestValidationPlanTool(
        catalog=_FakeCatalog(),
        list_changed_files=cast(Any, _FakeListFiles()),
        find_impacts=cast(Any, _FakeImpacts()),
    )
    result = tool.execute(SuggestValidationPlanRequest(change_set_id="cs-1"))
    plan = result.data
    assert plan.candidate_tests == ("tests/unit/test_app.py",)
    assert plan.suggested_commands[0].startswith("pytest ")
    assert any(item.startswith("ruff check") for item in plan.static_checks)
    assert "commands_not_executed" in plan.limitations
