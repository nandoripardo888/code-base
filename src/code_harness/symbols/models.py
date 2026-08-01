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
