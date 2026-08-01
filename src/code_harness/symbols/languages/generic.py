"""Generic best-effort extractor for unknown extensions."""

from __future__ import annotations

import re

from code_harness.symbols.models import Symbol, SymbolKind

_PATTERNS: tuple[tuple[re.Pattern[str], SymbolKind], ...] = (
    (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "class"),
    (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "interface"),
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "function"),
    (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "function"),
    (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b"), "function"),
    (re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:\w+\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\("), "function"),
)


class GenericExtractor:
    language = "generic"
    extensions: frozenset[str] = frozenset()

    def extract(self, path: str, source: str) -> list[Symbol]:
        symbols: list[Symbol] = []
        seen: set[tuple[str, int]] = set()
        for index, raw in enumerate(source.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith(("#", "//", "/*", "*")):
                continue
            for pattern, kind in _PATTERNS:
                match = pattern.match(raw)
                if not match:
                    continue
                name = match.group(1)
                key = (name, index)
                if key in seen:
                    break
                seen.add(key)
                symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        path=path,
                        line=index,
                        language=self.language,
                    )
                )
                break
        return symbols
