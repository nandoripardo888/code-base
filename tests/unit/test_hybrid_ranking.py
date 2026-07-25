from code_harness.application.context import estimate_tokens
from code_harness.application.dto.requests import BuildContextRequest
from code_harness.application.ranking import HybridCandidate, HybridRanker, QueryClassifier
from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import QueryPlan


def _candidate(
    path: str,
    line: int,
    match_type: MatchType,
    score: float,
    *,
    source_id: str | None = None,
    source_name: str | None = None,
) -> HybridCandidate:
    snippet = CodeSnippet(
        CodeLocation(path, line, line),
        "class AgendaService {}",
        "java",
        "hash",
    )
    return HybridCandidate(
        snippet,
        match_type,
        score,
        ("AgendaService",),
        "test candidate",
        source_id,
        source_name,
    )


def test_query_classifier_distinguishes_exact_conceptual_and_mixed_queries() -> None:
    classifier = QueryClassifier()

    exact = classifier.classify("AgendaService")
    conceptual = classifier.classify("como a agenda distribui os serviços disponíveis")
    mixed = classifier.classify("como AgendaService distribui os serviços")

    assert exact.kind is QueryKind.EXACT
    assert exact.identifiers == ("AgendaService",)
    assert conceptual.kind is QueryKind.CONCEPTUAL
    assert mixed.kind is QueryKind.MIXED
    assert "AgendaService" in mixed.identifiers
    assert classifier.classify("connection refused after timeout").kind is QueryKind.EXACT


def test_hybrid_ranker_prioritizes_and_merges_exact_symbol_evidence() -> None:
    classification = QueryClassifier().classify("AgendaService")
    candidates = (
        _candidate(
            "src/AgendaService.java",
            3,
            MatchType.SYMBOL,
            1.0,
            source_id="s1",
            source_name="AgendaService",
        ),
        _candidate("src/AgendaService.java", 3, MatchType.EXACT, 1.0),
        _candidate("docs/agenda.md", 1, MatchType.SEMANTIC, 0.99),
    )

    result = HybridRanker().rank(
        "AgendaService",
        classification,
        candidates,
        max_results=10,
    )

    assert result[0].snippet.location.path == "src/AgendaService.java"
    assert {item.match_type for item in result[0].evidence} == {
        MatchType.EXACT,
        MatchType.SYMBOL,
    }
    assert 0.0 < result[0].score < 1.0
    assert result[0].score_components is not None
    assert result[0].score_components.evidence == 0.995


def test_hybrid_ranker_applies_per_file_diversity_deterministically() -> None:
    classification = QueryClassifier().classify("como a agenda funciona em todos os módulos")
    candidates = tuple(
        [
            _candidate("src/agenda.py", line, MatchType.SEMANTIC, 1.0 - line / 100)
            for line in range(1, 8)
        ]
        + [_candidate("database/agenda.sql", 1, MatchType.SEMANTIC, 0.80)]
    )
    ranker = HybridRanker(max_results_per_file=2)

    first = ranker.rank("como a agenda funciona", classification, candidates, max_results=8)
    second = ranker.rank("como a agenda funciona", classification, candidates, max_results=8)

    assert first == second
    assert sum(hit.snippet.location.path == "src/agenda.py" for hit in first) == 2
    assert any(hit.snippet.location.path == "database/agenda.sql" for hit in first)


def test_token_estimate_and_context_request_are_conservative() -> None:
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("á") == 1
    request = BuildContextRequest("agenda", max_tokens=100, reserved_tokens=20)

    assert request.max_tokens - request.reserved_tokens == 80


def test_hybrid_ranker_favors_specific_term_over_broad_parent_symbol() -> None:
    query = "OS_AGENDA_TMP COD_TECNICO"
    classification = QueryClassifier().classify(query)
    broad = HybridCandidate(
        CodeSnippet(
            CodeLocation("src/OS_AGENDA_TMP.java", 1, 40),
            "private OS_AGENDA_TMP() {}\n",
            "java",
            "hash",
        ),
        MatchType.SYMBOL,
        1.0,
        ("OS_AGENDA_TMP",),
        "class symbol",
        "class",
        "OS_AGENDA_TMP",
        snippet_truncated=True,
        source_location=CodeLocation("src/OS_AGENDA_TMP.java", 1, 1200),
    )
    specific = HybridCandidate(
        CodeSnippet(
            CodeLocation("src/OS_AGENDA_TMP.java", 346, 352),
            "public Value getCOD_TECNICO() { return item1; }\n",
            "java",
            "hash",
        ),
        MatchType.SYMBOL,
        0.85,
        ("COD_TECNICO",),
        "specific symbol",
        "getter",
        "getCOD_TECNICO",
    )

    ranked = HybridRanker().rank(query, classification, (broad, specific), max_results=2)

    assert ranked[0].snippet.location.start_line == 346


def test_path_only_result_never_receives_maximum_score() -> None:
    query = "AgendaService"
    classification = QueryClassifier().classify(query)
    ranked = HybridRanker().rank(
        query,
        classification,
        (_candidate("src/AgendaService.java", 1, MatchType.PATH, 1.0),),
        max_results=1,
    )

    assert ranked[0].score == 0.45


def test_absolute_score_prioritizes_anchor_target_coverage_and_caps_comments() -> None:
    query = "WORK_ORDER_CURSOR TECHNICIAN_CODE"
    classification = QueryClassifier().classify(query)
    plan = QueryPlan(
        query_terms=("WORK_ORDER_CURSOR", "TECHNICIAN_CODE"),
        target_terms=("TECHNICIAN_CODE",),
        anchor_name="WORK_ORDER_CURSOR",
        anchor_path="src/WORK_ORDER_CURSOR.java",
    )
    constructor = HybridCandidate(
        CodeSnippet(
            CodeLocation("src/WORK_ORDER_CURSOR.java", 10, 10),
            "private WORK_ORDER_CURSOR() {}\n",
            "java",
            "hash",
        ),
        MatchType.SYMBOL,
        1.0,
        ("WORK_ORDER_CURSOR",),
        "constructor",
        scope="anchor",
        symbol_kind="constructor",
    )
    target = HybridCandidate(
        CodeSnippet(
            CodeLocation("src/WORK_ORDER_CURSOR.java", 80, 80),
            "void setFilterTECHNICIAN_CODE(Object value) {}\n",
            "java",
            "hash",
        ),
        MatchType.SYMBOL,
        0.9,
        ("TECHNICIAN_CODE",),
        "target member",
        scope="anchor",
        symbol_kind="method",
    )
    comment = HybridCandidate(
        CodeSnippet(
            CodeLocation("src/WORK_ORDER_CURSORRow.java", 5, 5),
            "/** WORK_ORDER_CURSOR TECHNICIAN_CODE */\n",
            "java",
            "hash",
        ),
        MatchType.REFERENCE,
        1.0,
        ("WORK_ORDER_CURSOR", "TECHNICIAN_CODE"),
        "comment",
        comment_only=True,
        reference_kind="comment_textual",
    )

    ranked = HybridRanker().rank(
        query,
        classification,
        (constructor, comment, target),
        max_results=3,
        plan=plan,
    )

    assert ranked[0].snippet.location.start_line == 80
    assert ranked[0].matched_terms == ("TECHNICIAN_CODE",)
    assert ranked[-1].comment_only is True
    assert ranked[-1].score <= 0.35
    assert all(hit.score < 1.0 for hit in ranked)
