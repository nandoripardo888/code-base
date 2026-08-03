"""Stable symbol model for extractors, stores, and future indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SymbolKind = Literal[
    "class",
    "function",
    "method",
    "interface",
    "type",
    "enum",
    "constant",
    "module",
]


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    kind: SymbolKind
    path: str
    line: int
    language: str
    container: str | None = None


ReferenceKind = Literal[
    "definition",
    "implementation",
    "instantiation",
    "call",
    "type_use",
    "import",
    "usage",
]


@dataclass(frozen=True, slots=True)
class Reference:
    name: str
    kind: ReferenceKind
    path: str
    line: int
    column: int
    language: str
    excerpt: str
    container: str | None = None
