from code_harness.application.context import estimate_tokens
from code_harness.application.dto.requests import BuildContextRequest
from code_harness.application.ranking import HybridCandidate, HybridRanker, QueryClassifier
from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation


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
    assert result[0].score == 1.0


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
