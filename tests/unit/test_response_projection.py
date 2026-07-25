import json

import pytest

from code_harness.domain.enums import CapabilityState, IndexState, MatchType
from code_harness.domain.models.capability import StrategyOutcome, ToolWarning
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead, TruncationInfo
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.context import ContextBundle, ContextSnippet
from code_harness.domain.models.hybrid import HybridSearchHit, SearchEvidence, SearchScore
from code_harness.domain.models.index_report import IndexStatus
from code_harness.domain.models.result_truncation import TruncationReason, truncation
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.response_projection import (
    ResponseDetail,
    apply_data_budget,
    resolve_response_detail,
    serialize_projected_result,
)


def _hybrid_hit(*, content: str = "def work():\n    return 1\n") -> HybridSearchHit:
    snippet = CodeSnippet(
        CodeLocation("src/work.py", 10, 11),
        content,
        "python",
        "file-hash",
    )
    evidence = SearchEvidence(
        MatchType.SYMBOL,
        1,
        1.0,
        1.0,
        0.25,
        "symbol-id",
        "work",
    )
    return HybridSearchHit(
        snippet,
        0.9,
        (evidence,),
        ("work",),
        "Matching symbol definition.",
        snippet_truncated=True,
        source_location=CodeLocation("src/work.py", 1, 500),
        scope="anchor",
        query_coverage=1.0,
        score_components=SearchScore(0.9, 1.0, 1.0, 0.01),
    )


def test_response_detail_precedence_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_RESPONSE_DETAIL", "detailed")

    assert resolve_response_detail() is ResponseDetail.DETAILED
    assert resolve_response_detail("minimal") is ResponseDetail.MINIMAL
    with pytest.raises(ValueError, match="response_detail"):
        resolve_response_detail("verbose")


def test_compact_projection_omits_internal_hybrid_metadata() -> None:
    payload = serialize_projected_result(
        ToolResult((_hybrid_hit(),), elapsed_ms=12, index_state="ready"),
        ResponseDetail.COMPACT,
    )

    assert payload["data"][0]["path"] == "src/work.py"
    assert payload["data"][0]["snippet_truncated"] is True
    assert payload["data"][0]["matched_terms"] == ["work"]
    assert payload["data"][0]["scope"] == "anchor"
    serialized = json.dumps(payload)
    assert "file_hash" not in serialized
    assert "symbol-id" not in serialized
    assert "elapsed_ms" not in serialized
    assert "source_location" not in serialized


def test_detailed_debug_and_full_profiles_keep_distinct_contracts() -> None:
    warning = ToolWarning("degraded", "Fallback used.", True, capability="search")
    strategy = StrategyOutcome("fts", CapabilityState.READY, hit_count=1, elapsed_ms=4)
    result = ToolResult(
        (_hybrid_hit(),),
        elapsed_ms=12,
        warnings=(warning,),
        index_state="ready",
        strategies=(strategy,),
    )

    detailed = serialize_projected_result(result, "detailed")
    debug = serialize_projected_result(result, "debug")
    full = serialize_projected_result(result, "full")

    assert detailed["data"][0]["source_location"]["end_line"] == 500
    assert detailed["data"][0]["query_coverage"] == 1.0
    assert detailed["data"][0]["score_components"]["evidence"] == 0.9
    assert detailed["warnings"][0]["message"] == "Fallback used."
    assert debug["diagnostics"]["elapsed_ms"] == 12
    assert debug["diagnostics"]["strategies"][0]["strategy"] == "fts"
    assert debug["data"][0]["snippet"]["file_hash"] == "file-hash"
    assert full["elapsed_ms"] == 12
    assert full["strategies"][0]["elapsed_ms"] == 4
    assert full["truncated"] is False


def test_numbered_source_read_has_one_representation_except_in_full() -> None:
    snippet = CodeSnippet(
        CodeLocation("src/work.py", 10, 11),
        "line one\nline two\n",
        "python",
        "hash",
    )
    source = SourceRead(
        snippet,
        truncation=TruncationInfo(False, total_lines=20),
        requested_range=(10, 11),
        actual_range=(10, 11),
        total_lines=20,
        numbered_lines=((10, "line one"), (11, "line two")),
    )
    result = ToolResult(source, elapsed_ms=1)

    compact = serialize_projected_result(result, "compact")
    debug = serialize_projected_result(result, "debug")
    full = serialize_projected_result(result, "full")

    assert compact["data"]["lines"] == [[10, "line one"], [11, "line two"]]
    assert "content" not in compact["data"]
    assert debug["data"]["file_hash"] == "hash"
    assert "content" not in debug["data"]
    assert full["data"]["snippet"]["content"] == "line one\nline two\n"
    assert full["data"]["numbered_lines"][0] == [10, "line one"]


def test_budget_drops_tail_results_and_reports_omissions() -> None:
    data = [{"path": f"src/{index}.py", "content": "x" * 200} for index in range(10)]

    budgeted = apply_data_budget(data, max_chars=700)

    assert budgeted.truncated is True
    assert budgeted.omitted_results > 0
    assert len(json.dumps(budgeted.data, ensure_ascii=True, separators=(",", ":"))) <= 700
    assert budgeted.data[0]["path"] == "src/0.py"


def test_full_profile_bypasses_aggregate_budget() -> None:
    result = ToolResult(({"content": "x" * 31_000},), elapsed_ms=1)

    compact = serialize_projected_result(result, "compact")
    full = serialize_projected_result(result, "full")

    assert compact["truncated"] is True
    assert compact["omitted_results"] == 1
    assert compact["truncation"]["reasons"] == ["response_budget"]
    assert full["data"][0]["content"] == "x" * 31_000


def test_compact_projection_explains_known_truncation() -> None:
    result = ToolResult(
        ("first", "second"),
        elapsed_ms=1,
        truncation=truncation(
            TruncationReason.RESULT_LIMIT,
            results=True,
            omitted_results=3,
        ),
    )

    compact = serialize_projected_result(result, "compact")

    assert compact["truncated"] is True
    assert compact["truncation"] == {
        "results": True,
        "reasons": ["result_limit"],
        "omitted_results": 3,
    }
    assert compact["omitted_results"] == 3


def test_status_projection_exposes_runtime_identity_without_internal_index_ids() -> None:
    status = IndexStatus(
        "project-id",
        IndexState.READY,
        4,
        10,
        10,
        0,
        service_version="0.2.0",
        build_commit="abc123",
        service_started_at="2026-07-24T12:00:00+00:00",
        service_instance_id="instance-1",
    )

    compact = serialize_projected_result(ToolResult(status, 1), "compact")

    assert compact["data"]["service_version"] == "0.2.0"
    assert compact["data"]["build_commit"] == "abc123"
    assert compact["data"]["service_instance_id"] == "instance-1"
    assert "project_id" not in compact["data"]


def test_compact_context_budget_keeps_count_invariant() -> None:
    snippets = tuple(
        ContextSnippet(
            CodeSnippet(
                CodeLocation(f"src/{index}.py", 1, 1),
                "x" * 5_000,
                "python",
                f"hash-{index}",
            ),
            0.9,
            "primary",
            None,
            0,
            1_700,
            "test",
        )
        for index in range(10)
    )
    bundle = ContextBundle(
        "query",
        snippets,
        0,
        17_000,
        20_000,
        considered_results=10,
        selected_results=10,
    )

    compact = serialize_projected_result(ToolResult(bundle, 1), "compact")
    data = compact["data"]

    assert compact["truncated"] is True
    assert data["considered_results"] == (
        data["selected_results"] + data["omitted_results"]
    )
    assert data["omitted"]["response_budget"] == data["omitted_results"]
