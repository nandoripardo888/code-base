"""Language extractor protocol and extension registry."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from code_harness.symbols.models import Symbol


class LanguageExtractor(Protocol):
    language: str
    extensions: frozenset[str]

    def extract(self, path: str, source: str) -> list[Symbol]:
        """Parse one file; path is project-relative for Symbol.path."""


class ExtractorRegistry:
    def __init__(self) -> None:
        self._by_extension: dict[str, LanguageExtractor] = {}
        self._fallback: LanguageExtractor | None = None

    def register(self, extractor: LanguageExtractor, *, fallback: bool = False) -> None:
        if fallback:
            self._fallback = extractor
            return
        for extension in extractor.extensions:
            key = extension.lower()
            if not key.startswith("."):
                key = f".{key}"
            self._by_extension[key] = extractor

    def for_path(self, path: str) -> LanguageExtractor:
        extension = Path(path).suffix.lower()
        extractor = self._by_extension.get(extension)
        if extractor is not None:
            return extractor
        if self._fallback is None:
            raise RuntimeError("ExtractorRegistry has no fallback extractor.")
        return self._fallback

    def extract(self, path: str, source: str) -> list[Symbol]:
        try:
            return self.for_path(path).extract(path, source)
        except Exception:
            return []
