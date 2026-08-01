"""Built-in language extractors."""

from __future__ import annotations

from code_harness.symbols.extractors import ExtractorRegistry
from code_harness.symbols.languages.generic import GenericExtractor
from code_harness.symbols.languages.javascript import JavaScriptExtractor
from code_harness.symbols.languages.python import PythonExtractor


def build_default_registry() -> ExtractorRegistry:
    registry = ExtractorRegistry()
    registry.register(PythonExtractor())
    registry.register(JavaScriptExtractor())
    registry.register(GenericExtractor(), fallback=True)
    return registry
