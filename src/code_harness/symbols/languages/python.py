"""Python symbol extractor (regex, best-effort)."""

from __future__ import annotations

import re

from code_harness.symbols.models import Symbol, SymbolKind

_CLASS = re.compile(r"^(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_CONST = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=")


class PythonExtractor:
    language = "python"
    extensions = frozenset({".py"})

    def extract(self, path: str, source: str) -> list[Symbol]:
        symbols: list[Symbol] = []
        class_stack: list[tuple[int, str]] = []  # (indent, name)

        for index, raw in enumerate(source.splitlines(), start=1):
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            class_match = _CLASS.match(line)
            if class_match:
                indent = len(class_match.group(1).replace("\t", "    "))
                name = class_match.group(2)
                _pop_deeper(class_stack, indent)
                class_stack.append((indent, name))
                symbols.append(
                    Symbol(
                        name=name,
                        kind="class",
                        path=path,
                        line=index,
                        language=self.language,
                    )
                )
                continue

            def_match = _DEF.match(line)
            if def_match:
                indent = len(def_match.group(1).replace("\t", "    "))
                name = def_match.group(2)
                _pop_deeper(class_stack, indent)
                container = class_stack[-1][1] if class_stack and indent > class_stack[-1][0] else None
                kind: SymbolKind = "method" if container else "function"
                symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        path=path,
                        line=index,
                        language=self.language,
                        container=container,
                    )
                )
                continue

            if class_stack:
                continue
            const_match = _CONST.match(line.strip())
            if const_match and not line.startswith((" ", "\t")):
                symbols.append(
                    Symbol(
                        name=const_match.group(1),
                        kind="constant",
                        path=path,
                        line=index,
                        language=self.language,
                    )
                )

        return symbols


def _pop_deeper(stack: list[tuple[int, str]], indent: int) -> None:
    while stack and stack[-1][0] >= indent:
        stack.pop()
