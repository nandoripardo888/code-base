"""JavaScript / TypeScript symbol extractor (regex, best-effort)."""

from __future__ import annotations

import re

from code_harness.symbols.models import Symbol, SymbolKind

_CLASS = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
_INTERFACE = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_TYPE = re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_ENUM = re.compile(r"^\s*(?:export\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_][A-Za-z0-9_]*)\b"
)
_CONST_FN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
)
_METHOD = re.compile(
    r"^\s+(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\("
)

_SKIP_METHODS = frozenset({"if", "for", "while", "switch", "catch", "function", "constructor"})


class JavaScriptExtractor:
    language = "javascript"
    extensions = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})

    def extract(self, path: str, source: str) -> list[Symbol]:
        symbols: list[Symbol] = []
        current_class: str | None = None
        class_indent: int | None = None

        for index, raw in enumerate(source.splitlines(), start=1):
            line = raw.split("//", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" \t"))

            if class_indent is not None and indent <= class_indent and line.strip().startswith("}"):
                current_class = None
                class_indent = None

            match = _CLASS.match(line)
            if match:
                current_class = match.group(1)
                class_indent = indent
                symbols.append(_sym(path, index, match.group(1), "class", self.language))
                continue

            match = _INTERFACE.match(line)
            if match:
                symbols.append(_sym(path, index, match.group(1), "interface", self.language))
                continue

            match = _TYPE.match(line)
            if match:
                symbols.append(_sym(path, index, match.group(1), "type", self.language))
                continue

            match = _ENUM.match(line)
            if match:
                symbols.append(_sym(path, index, match.group(1), "enum", self.language))
                continue

            match = _FUNCTION.match(line)
            if match:
                symbols.append(_sym(path, index, match.group(1), "function", self.language))
                continue

            match = _CONST_FN.match(line)
            if match:
                symbols.append(_sym(path, index, match.group(1), "function", self.language))
                continue

            if current_class is not None:
                method = _METHOD.match(line)
                if method and method.group(1) not in _SKIP_METHODS:
                    symbols.append(
                        _sym(
                            path,
                            index,
                            method.group(1),
                            "method",
                            self.language,
                            container=current_class,
                        )
                    )

        return symbols


def _sym(
    path: str,
    line: int,
    name: str,
    kind: SymbolKind,
    language: str,
    container: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        path=path,
        line=line,
        language=language,
        container=container,
    )
