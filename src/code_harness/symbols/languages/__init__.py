"""Built-in language extractors."""

from __future__ import annotations

from code_harness.symbols.extractors import ExtractorRegistry
from code_harness.symbols.languages.generic import GenericExtractor
from code_harness.symbols.languages.javascript import JavaScriptExtractor
from code_harness.symbols.languages.python import PythonExtractor
from code_harness.symbols.parsers import ParserSymbolExtractor, TreeSitterRegistry


def build_default_registry() -> ExtractorRegistry:
    registry = ExtractorRegistry()
    python = PythonExtractor()
    javascript = JavaScriptExtractor()
    generic = GenericExtractor()
    parsers = TreeSitterRegistry()
    registry.register(
        ParserSymbolExtractor(
            language="python",
            extensions=python.extensions,
            fallback=python,
            registry=parsers,
        )
    )
    registry.register(
        ParserSymbolExtractor(
            language="javascript",
            extensions=javascript.extensions,
            fallback=javascript,
            registry=parsers,
        )
    )
    registry.register(
        ParserSymbolExtractor(
            language="java",
            extensions=frozenset({".java"}),
            fallback=generic,
            registry=parsers,
        )
    )
    registry.register(generic, fallback=True)
    return registry
