from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from code_harness.errors import (
    InvalidArgumentError,
    ParserSupportUnavailableError,
    UnsupportedReferenceLanguageError,
)
from code_harness.paths import PathGuard
from code_harness.symbols.parsers import TreeSitterRegistry
from code_harness.tools import grep
from tests.conftest import requires_parsers, requires_ripgrep


def test_missing_parser_extra_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def missing_runtime(name: str):  # type: ignore[no-untyped-def]
        if name == "tree_sitter":
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr("code_harness.symbols.parsers.importlib.import_module", missing_runtime)
    with pytest.raises(ParserSupportUnavailableError, match=r"code-harness\[parsers\]"):
        TreeSitterRegistry().require_runtime()


@requires_ripgrep
@requires_parsers
def test_python_references_classify_code_but_not_comments(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.py").write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return Helper()\n"
        "\n"
        "from demo import Helper\n"
        "# Helper() is documentation\n"
        "text = 'Helper()'\n",
        encoding="utf-8",
    )
    result = grep(PathGuard(root), pattern="Helper", output_mode="references")
    assert "[call in Service.run]" in result
    assert "[import]" in result
    assert "documentation" not in result
    assert "text =" not in result


@requires_ripgrep
@requires_parsers
def test_references_are_case_optional_and_keep_homonyms(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "one.py").write_text("class Target: pass\nTarget()\n", encoding="utf-8")
    (root / "two.py").write_text("def target(): pass\ntarget()\n", encoding="utf-8")
    result = grep(
        PathGuard(root),
        pattern="TARGET",
        output_mode="references",
        case_insensitive=True,
    )
    assert "4 syntactic occurrences in 2 files" in result
    assert "one.py" in result
    assert "two.py" in result


@requires_ripgrep
@requires_parsers
def test_references_keep_valid_nodes_from_partial_tree(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "partial.py").write_text(
        "def valid():\n    Target()\n\ndef broken(:\n",
        encoding="utf-8",
    )
    result = grep(PathGuard(root), pattern="Target", output_mode="references")
    assert "[call in valid]" in result
    assert "with syntax errors" in result


@requires_ripgrep
@requires_parsers
def test_references_reject_regex_patterns(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(InvalidArgumentError, match="exact identifier"):
        grep(PathGuard(root), pattern="Foo|Bar", output_mode="references")


@requires_ripgrep
@requires_parsers
@pytest.mark.parametrize(
    ("filename", "source", "expected"),
    [
        (
            "Sample.java",
            "import demo.Helper; class Sample { Helper make() { return new Helper(); } }",
            "[instantiation in Sample.make]",
        ),
        (
            "sample.js",
            "class Helper {}\nfunction make() { return new Helper(); }\n",
            "[definition]",
        ),
        (
            "sample.ts",
            "interface Helper {}\nfunction use(value: Helper) { return value; }\n",
            "[type_use in use]",
        ),
        (
            "sample.tsx",
            "interface Helper {}\nconst View = (value: Helper) => <div />;\n",
            "[type_use]",
        ),
    ],
)
def test_references_support_all_selected_languages(
    tmp_path: Path,
    filename: str,
    source: str,
    expected: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / filename).write_text(source, encoding="utf-8")
    result = grep(PathGuard(root), pattern="Helper", output_mode="references")
    assert expected in result


@requires_ripgrep
@requires_parsers
def test_references_respect_exclude_and_paging(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "ignored").mkdir(parents=True)
    (root / "a.py").write_text("Target()\nTarget()\n", encoding="utf-8")
    (root / "ignored" / "b.py").write_text("Target()\n", encoding="utf-8")
    guard = PathGuard(root)
    first = grep(
        guard,
        pattern="Target",
        output_mode="references",
        exclude="ignored/**",
        head_limit=1,
    )
    assert "2 syntactic occurrences in 1 file" in first
    assert "ignored/b.py" not in first
    assert "(1 more references; pass offset=1)" in first


@requires_ripgrep
@requires_parsers
def test_references_reject_unsupported_direct_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("Target", encoding="utf-8")
    with pytest.raises(UnsupportedReferenceLanguageError):
        grep(
            PathGuard(root),
            pattern="Target",
            path="README.md",
            output_mode="references",
        )


@requires_ripgrep
@requires_parsers
def test_java_outline_uses_optional_parser(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Sample.java").write_text(
        "class Sample { void run() {} }",
        encoding="utf-8",
    )
    result = grep(PathGuard(root), path="Sample.java", output_mode="symbols")
    assert "class Sample" in result
    assert "method Sample.run" in result


@requires_ripgrep
@requires_parsers
def test_java_outline_deduplicates_methods_and_ignores_exception_constructors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Sample.java").write_text(
        "class Sample {\n"
        "  void solicitarAssinatura() {}\n"
        "  void fail() { throw new DataException(); }\n"
        "}\n",
        encoding="utf-8",
    )

    result = grep(PathGuard(root), path="Sample.java", output_mode="symbols")

    assert result.count("solicitarAssinatura") == 1
    assert "method Sample.solicitarAssinatura" in result
    assert "function DataException" not in result


@requires_ripgrep
@requires_parsers
def test_chained_method_below_new_expression_is_a_call(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Sample.java").write_text(
        "class Sample { void run() { new Service().getStatusAssinatura(); } }",
        encoding="utf-8",
    )

    result = grep(
        PathGuard(root),
        pattern="getStatusAssinatura",
        output_mode="references",
    )

    assert "[call in Sample.run]" in result
    assert "[instantiation" not in result


@requires_ripgrep
@requires_parsers
def test_references_classify_implementations_and_filter_kinds(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Sample.java").write_text(
        "import demo.Contract; interface Contract {} class Service implements Contract {}",
        encoding="utf-8",
    )
    guard = PathGuard(root)

    implementations = grep(
        guard,
        pattern="Contract",
        output_mode="references",
        reference_kind="implementation",
    )
    without_imports = grep(
        guard,
        pattern="Contract",
        output_mode="references",
        reference_kind="definition,implementation,import",
        exclude_reference_kind="import",
    )

    assert "1 syntactic occurrence in 1 file" in implementations
    assert "[implementation in Service]" in implementations
    assert "[definition]" not in implementations
    assert "[import]" not in without_imports
    assert "[definition]" in without_imports
    assert "[implementation in Service]" in without_imports


@requires_ripgrep
@requires_parsers
def test_references_reject_invalid_kind_filter(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.py").write_text("Target()\n", encoding="utf-8")

    with pytest.raises(InvalidArgumentError, match="invalid reference kind"):
        grep(
            PathGuard(root),
            pattern="Target",
            output_mode="references",
            reference_kind="constructor",
        )
