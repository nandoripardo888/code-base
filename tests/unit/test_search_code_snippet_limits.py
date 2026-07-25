import pytest

from code_harness.application.dto.requests import SearchCodeRequest
from code_harness.application.tools.search_code import _bounded_snippet
from code_harness.domain.enums import QueryKind
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import QueryClassification
from code_harness.domain.models.index_report import IndexedSource


def _source(language: str) -> IndexedSource:
    lines = ["class LargeType {\n"]
    lines.extend(f"    int filler_{index};\n" for index in range(1, 1_202))
    lines[899] = '    String universalNeedle = "universalNeedle";\n'
    lines[900] = "    void universalNeedleHandler() {}\n"
    lines.append("}\n")
    content = "".join(lines)
    return IndexedSource(
        f"src/LargeType.{language}",
        content,
        len(content),
        1,
        language,
        "utf-8",
        "hash",
    )


def _classification(query: str) -> QueryClassification:
    return QueryClassification(
        QueryKind.EXACT,
        ("strong_identifier",),
        (query,),
        (query,),
        (),
    )


@pytest.mark.parametrize("language", ["java", "python"])
def test_match_window_limits_large_symbols_and_keeps_relevant_term(language: str) -> None:
    source = _source(language)
    request = SearchCodeRequest("universalNeedle")

    snippet, truncated, original = _bounded_snippet(
        source,
        CodeLocation(source.path, 1, 1_203),
        request,
        _classification(request.query),
    )

    assert truncated is True
    assert original == CodeLocation(source.path, 1, 1_203)
    assert "universalNeedle" in snippet.content
    assert len(snippet.content) <= 6_000
    assert snippet.location.end_line - snippet.location.start_line + 1 <= 40


def test_match_window_falls_back_to_declaration_without_an_internal_match() -> None:
    source = _source("java")
    request = SearchCodeRequest("not-present")

    snippet, truncated, original = _bounded_snippet(
        source,
        CodeLocation(source.path, 1, 1_203),
        request,
        _classification(request.query),
    )

    assert truncated is True
    assert original is not None
    assert snippet.location.start_line == 1
    assert "class LargeType" in snippet.content


def test_symbol_and_none_modes_remain_safe() -> None:
    source = _source("java")
    classification = _classification("universalNeedle")

    symbol, truncated, _ = _bounded_snippet(
        source,
        CodeLocation(source.path, 1, 1_203),
        SearchCodeRequest("universalNeedle", snippet_mode="symbol"),
        classification,
    )
    none, omitted, _ = _bounded_snippet(
        source,
        CodeLocation(source.path, 1, 1_203),
        SearchCodeRequest("universalNeedle", snippet_mode="none"),
        classification,
    )

    assert truncated is True
    assert symbol.location.start_line == 1
    assert len(symbol.content) <= 6_000
    assert none.content == ""
    assert omitted is False
