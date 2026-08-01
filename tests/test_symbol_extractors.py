"""Unit tests for language symbol extractors (no ripgrep)."""

from __future__ import annotations

from code_harness.symbols.extractors import ExtractorRegistry
from code_harness.symbols.languages import build_default_registry
from code_harness.symbols.languages.generic import GenericExtractor
from code_harness.symbols.languages.javascript import JavaScriptExtractor
from code_harness.symbols.languages.python import PythonExtractor


def test_python_extractor_finds_class_function_method_constant() -> None:
    source = """
VALUE = 1

class App:
    def run(self):
        return 1

def helper():
    pass
"""
    symbols = PythonExtractor().extract("src/app.py", source)
    kinds = {(s.kind, s.name, s.container) for s in symbols}
    assert ("constant", "VALUE", None) in kinds
    assert ("class", "App", None) in kinds
    assert ("method", "run", "App") in kinds
    assert ("function", "helper", None) in kinds


def test_javascript_extractor_finds_class_interface_function() -> None:
    source = """
export interface Options {
  n: number;
}

export class App {
  save() {
    return 1;
  }
}

export function run() {
  return 2;
}

export const load = () => 3;
"""
    symbols = JavaScriptExtractor().extract("src/app.ts", source)
    by_name = {s.name: s for s in symbols}
    assert by_name["Options"].kind == "interface"
    assert by_name["App"].kind == "class"
    assert by_name["save"].kind == "method"
    assert by_name["save"].container == "App"
    assert by_name["run"].kind == "function"
    assert by_name["load"].kind == "function"


def test_generic_extractor_finds_class_and_fn() -> None:
    source = "class Foo {}\nfn bar() {}\n"
    symbols = GenericExtractor().extract("main.rs", source)
    names = {s.name for s in symbols}
    assert "Foo" in names
    assert "bar" in names


def test_registry_routes_extensions_and_fallback() -> None:
    registry = build_default_registry()
    assert registry.for_path("a.py").language == "python"
    assert registry.for_path("a.ts").language == "javascript"
    assert registry.for_path("a.unknown").language == "generic"


def test_registry_extract_swallows_extractor_errors() -> None:
    class Boom:
        language = "boom"
        extensions = frozenset({".boom"})

        def extract(self, path: str, source: str) -> list:
            raise RuntimeError("boom")

    registry = ExtractorRegistry()
    registry.register(Boom())
    registry.register(GenericExtractor(), fallback=True)
    assert registry.extract("x.boom", "class A {}") == []
