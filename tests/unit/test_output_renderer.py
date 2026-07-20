import json

from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.context import ContextBundle, ContextSnippet
from code_harness.domain.models.hybrid import HybridSearchHit, SearchEvidence
from code_harness.domain.models.repository_map import (
    RepositoryDirectory,
    RepositoryFile,
    RepositoryMap,
    RepositorySymbol,
)
from code_harness.interfaces.cli.renderers.output import OutputFormat, render_value


def test_json_output_is_console_safe_and_preserves_unicode(capsys) -> None:
    value = {"text": "dicionário semântico — ação"}

    render_value(value, OutputFormat.JSON)

    rendered = capsys.readouterr().out
    assert rendered.isascii()
    assert json.loads(rendered) == value


def test_text_renderer_handles_hybrid_context_and_repository_tree(capsys) -> None:
    snippet = CodeSnippet(CodeLocation("src/app.py", 1, 2), "def app():\n    pass\n", "python", "h")
    evidence = SearchEvidence(MatchType.SYMBOL, 1, 1.0, 1.0, 0.1, "s", "app")
    hybrid = HybridSearchHit(snippet, 1.0, (evidence,), ("app",), "Matching symbol.")
    context = ContextBundle(
        "app",
        (
            ContextSnippet(
                snippet,
                1.0,
                "definition",
                "app",
                0,
                10,
                "Matching symbol.",
                (MatchType.SYMBOL,),
            ),
        ),
        1,
        10,
        100,
    )
    repository_map = RepositoryMap(
        RepositoryDirectory(
            ".",
            "",
            (
                RepositoryDirectory(
                    "src",
                    "src",
                    files=(
                        RepositoryFile(
                            "app.py",
                            "src/app.py",
                            "python",
                            20,
                            (RepositorySymbol("app", "app", "function", 1, 2),),
                        ),
                    ),
                ),
            ),
        ),
        1,
        1,
        0,
        "ready",
    )

    render_value((hybrid,), OutputFormat.TEXT)
    render_value(context, OutputFormat.LLM)
    render_value(repository_map, OutputFormat.TEXT)

    rendered = capsys.readouterr().out
    assert "[hybrid 1.00; symbol]" in rendered
    assert "estimated_tokens=10/100" in rendered
    assert "src/" in rendered
    assert "function app" in rendered


def test_jsonl_renderer_serializes_tuple_items(capsys) -> None:
    render_value(({"value": 1}, {"value": 2}), OutputFormat.JSONL)

    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line) for line in lines] == [{"value": 1}, {"value": 2}]
