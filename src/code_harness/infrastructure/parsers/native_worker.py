"""Isolated structural parser worker.

This module is the only process boundary allowed to load parser implementations.
It intentionally communicates through one JSON request and one JSON response.
"""

import ast
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Any

from code_harness.infrastructure.parsers.signature_extractor import (
    SIGNATURE_EXTRACTOR_VERSION,
    canonicalize_java,
    canonicalize_plsql,
    canonicalize_python,
    extract_header,
    java_signatures_from_node,
    normalize_display,
)


@dataclass(frozen=True, slots=True)
class _Symbol:
    symbol_id: str
    name: str
    qualified_name: str | None
    kind: str
    start_line: int
    end_line: int
    signature: str | None
    parent_symbol_id: str | None
    canonical_signature: str | None = None


@dataclass(frozen=True, slots=True)
class _Reference:
    reference_id: str
    target_name: str
    kind: str
    line: int
    column: int
    source_symbol_id: str | None = None


def _identifier(*parts: object) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _location(path: str, start: int, end: int, column: int = 1) -> dict[str, object]:
    return {
        "path": path,
        "start_line": max(1, start),
        "end_line": max(start, end),
        "start_column": max(1, column),
        "end_column": None,
    }


def _symbol_payload(path: str, symbol: _Symbol) -> dict[str, object]:
    return {
        "symbol_id": symbol.symbol_id,
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind,
        "location": _location(path, symbol.start_line, symbol.end_line),
        "signature": symbol.signature,
        "parent_symbol_id": symbol.parent_symbol_id,
        "canonical_signature": symbol.canonical_signature,
    }


def _reference_payload(path: str, reference: _Reference) -> dict[str, object]:
    return {
        "reference_id": reference.reference_id,
        "target_name": reference.target_name,
        "kind": reference.kind,
        "location": _location(path, reference.line, reference.line, reference.column),
        "source_symbol_id": reference.source_symbol_id,
    }


def _chunk_payload(path: str, content: str, symbol: _Symbol) -> dict[str, object]:
    lines = content.splitlines(keepends=True)
    body = "".join(lines[symbol.start_line - 1 : symbol.end_line])
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return {
        "chunk_id": _identifier(path, symbol.symbol_id, content_hash, "structural-v1"),
        "location": _location(path, symbol.start_line, symbol.end_line),
        "content": body,
        "content_hash": content_hash,
        "kind": "symbol",
        "symbol_id": symbol.symbol_id,
        "parent_chunk_id": None,
    }


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.lines = content.splitlines()
        self.symbols: list[_Symbol] = []
        self.references: list[_Reference] = []
        self._parents: list[_Symbol] = []

    def _add_symbol(
        self,
        node: ast.AST,
        name: str,
        kind: str,
        *,
        args: ast.arguments | None = None,
        returns: ast.AST | None = None,
    ) -> _Symbol:
        start = int(getattr(node, "lineno", 1))
        end = int(getattr(node, "end_lineno", start))
        parent = self._parents[-1] if self._parents else None
        qualified = ".".join([*(item.name for item in self._parents), name])
        display = normalize_display(extract_header("\n".join(self.lines), start))
        if not display and start <= len(self.lines):
            display = self.lines[start - 1].strip()
        canonical = (
            canonicalize_python(
                qualified_name=qualified,
                args=args,
                returns=returns,
                display_signature=display,
            )
            if kind in {"function", "method"}
            else qualified
        )
        symbol = _Symbol(
            _identifier(self.path, kind, qualified, start),
            name,
            qualified,
            kind,
            start,
            end,
            display or None,
            parent.symbol_id if parent else None,
            canonical,
        )
        self.symbols.append(symbol)
        return symbol

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = self._add_symbol(node, node.name, "class")
        self._parents.append(symbol)
        self.generic_visit(node)
        self._parents.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self._parents and self._parents[-1].kind == "class" else "function"
        symbol = self._add_symbol(
            node,
            node.name,
            kind,
            args=node.args,
            returns=node.returns,
        )
        self._parents.append(symbol)
        self.generic_visit(node)
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        name: str | None = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name:
            source = self._parents[-1].symbol_id if self._parents else None
            self.references.append(
                _Reference(
                    _identifier(self.path, "call", name, node.lineno, node.col_offset),
                    name,
                    "call",
                    node.lineno,
                    node.col_offset + 1,
                    source,
                )
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias_index, alias in enumerate(node.names):
            self.references.append(
                _Reference(
                    _identifier(self.path, "import", alias.name, node.lineno, alias_index),
                    alias.name,
                    "import",
                    node.lineno,
                    node.col_offset + 1,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias_index, alias in enumerate(node.names):
            target = f"{module}.{alias.name}".strip(".")
            self.references.append(
                _Reference(
                    _identifier(self.path, "import", target, node.lineno, alias_index),
                    target,
                    "import",
                    node.lineno,
                    node.col_offset + 1,
                )
            )


def _analyze_python(path: str, content: str) -> tuple[list[_Symbol], list[_Reference], list[str]]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as error:
        return [], [], [f"Python syntax error at line {error.lineno or 1}: {error.msg}."]
    visitor = _PythonVisitor(path, content)
    visitor.visit(tree)
    return visitor.symbols, visitor.references, []


def _tree_sitter_analysis(
    path: str, language: str, content: str
) -> tuple[list[_Symbol], list[_Reference], list[str]] | None:
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        grammar = importlib.import_module(f"tree_sitter_{language}")
    except ImportError:
        return None
    source = content.encode("utf-8")
    language_object = tree_sitter.Language(grammar.language())
    parser = tree_sitter.Parser(language_object)
    root = parser.parse(source).root_node
    symbols: list[_Symbol] = []
    references: list[_Reference] = []
    symbol_types = {
        "java": {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
            "method_declaration": "method",
            "constructor_declaration": "constructor",
            "field_declaration": "field",
        },
        "python": {
            "class_definition": "class",
            "function_definition": "function",
        },
    }[language]

    def node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def point_value(point: Any, index: int) -> int:
        try:
            return int(point[index])
        except TypeError:
            return int(point.row if index == 0 else point.column)

    def visit(node: Any, parent: _Symbol | None = None) -> None:
        active_parent = parent
        symbol_kind = symbol_types.get(node.type)
        if symbol_kind is not None:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = node_text(name_node)
                if language == "python" and symbol_kind == "function" and parent is not None:
                    symbol_kind = "method"
                start = point_value(node.start_point, 0) + 1
                end = max(start, point_value(node.end_point, 0) + 1)
                qualified = f"{parent.qualified_name}.{name}" if parent else name
                if language == "java" and symbol_kind in {"method", "constructor", "function"}:
                    display, canonical = java_signatures_from_node(
                        node,
                        source,
                        qualified_name=qualified,
                        content=content,
                        start_line=start,
                    )
                else:
                    display = normalize_display(extract_header(content, start))
                    if not display:
                        display = node_text(node).splitlines()[0].strip()
                    if language == "python" and symbol_kind in {"method", "function"}:
                        canonical = canonicalize_python(
                            qualified_name=qualified,
                            display_signature=display,
                        )
                    else:
                        canonical = qualified
                active_parent = _Symbol(
                    _identifier(path, symbol_kind, qualified, start),
                    name,
                    qualified,
                    symbol_kind,
                    start,
                    end,
                    display,
                    parent.symbol_id if parent else None,
                    canonical,
                )
                symbols.append(active_parent)
        reference_kind: str | None = None
        reference_node: Any | None = None
        if language == "java" and node.type == "method_invocation":
            reference_kind = "call"
            reference_node = node.child_by_field_name("name")
        elif language == "python" and node.type == "call":
            reference_kind = "call"
            reference_node = node.child_by_field_name("function")
        elif node.type in {"import_declaration", "import_statement", "import_from_statement"}:
            reference_kind = "import"
            reference_node = node
        if reference_kind and reference_node is not None:
            raw_name = node_text(reference_node).strip().removeprefix("import ")
            name = raw_name.rstrip(";").split(".")[-1]
            if name:
                line = point_value(reference_node.start_point, 0) + 1
                column = point_value(reference_node.start_point, 1) + 1
                references.append(
                    _Reference(
                        _identifier(
                            path,
                            reference_kind,
                            name,
                            line,
                            column,
                            reference_node.start_byte,
                            reference_node.end_byte,
                        ),
                        name,
                        reference_kind,
                        line,
                        column,
                        parent.symbol_id if parent else None,
                    )
                )
        for child in node.children:
            visit(child, active_parent)

    visit(root)
    warnings = ["Tree-sitter reported syntax errors."] if root.has_error else []
    return symbols, references, warnings


_JAVA_TYPE = re.compile(r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_JAVA_METHOD = re.compile(
    r"(?ms)^\s*(?:@[\w.]+\s*)*(?:(?:public|protected|private|static|final|abstract|"
    r"synchronized|native|default|strictfp)\s+)*(?:[\w$<>\[\],.?\s]+\s+)?"
    r"([A-Za-z_$][\w$]*)\s*\([^;{}]*?\)\s*(?:throws\s+[^\{]+)?\{"
)
_JAVA_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)\s*;")
_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _brace_end(content: str, start: int) -> int:
    opening = content.find("{", start)
    if opening < 0:
        return _line_number(content, start)
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(content)):
        char = content[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return _line_number(content, index)
    return max(1, len(content.splitlines()))


def _analyze_java(path: str, content: str) -> tuple[list[_Symbol], list[_Reference], list[str]]:
    symbols: list[_Symbol] = []
    references: list[_Reference] = []
    types: list[_Symbol] = []
    for match in _JAVA_TYPE.finditer(content):
        kind, name = match.groups()
        start = _line_number(content, match.start())
        symbol = _Symbol(
            _identifier(path, kind, name, start),
            name,
            name,
            kind,
            start,
            _brace_end(content, match.start()),
            content.splitlines()[start - 1].strip(),
            None,
        )
        types.append(symbol)
        symbols.append(symbol)
    declaration_offsets: set[int] = set()
    for match in _JAVA_METHOD.finditer(content):
        name = match.group(1)
        if name in {"if", "for", "while", "switch", "catch", "try", "synchronized"}:
            continue
        start = _line_number(content, match.start())
        end = _brace_end(content, match.start())
        parent = next(
            (item for item in reversed(types) if item.start_line <= start <= item.end_line), None
        )
        kind = "constructor" if parent and name == parent.name else "method"
        qualified = f"{parent.name}.{name}" if parent else name
        display = normalize_display(extract_header(content, start))
        if not display:
            display = content.splitlines()[start - 1].strip()
        symbols.append(
            _Symbol(
                _identifier(path, kind, qualified, start),
                name,
                qualified,
                kind,
                start,
                end,
                display,
                parent.symbol_id if parent else None,
                canonicalize_java(qualified_name=qualified, display_signature=display),
            )
        )
        declaration_offsets.add(match.start(1))
    for match in _JAVA_IMPORT.finditer(content):
        name = match.group(1)
        line = _line_number(content, match.start())
        references.append(
            _Reference(_identifier(path, "import", name, line), name, "import", line, 1)
        )
    for match in _CALL.finditer(content):
        name = match.group(1)
        if match.start() in declaration_offsets or name in {
            "if",
            "for",
            "while",
            "switch",
            "catch",
        }:
            continue
        line = _line_number(content, match.start())
        parent = next(
            (item for item in reversed(symbols) if item.start_line <= line <= item.end_line), None
        )
        references.append(
            _Reference(
                _identifier(path, "call", name, line, match.start()),
                name,
                "call",
                line,
                1,
                parent.symbol_id if parent else None,
            )
        )
    warnings = [] if symbols else ["No Java structural declarations were recognized."]
    return symbols, references, warnings


_PLSQL_SYMBOL = re.compile(
    r"(?im)^\s*(?:create\s+(?:or\s+replace\s+)?)?"
    r"(package\s+body|package|procedure|function|trigger|cursor)\s+"
    r"([A-Za-z][\w$#]*)"
)
_PLSQL_CALL = re.compile(r"\b([A-Za-z][\w$#]*)\s*\(", re.IGNORECASE)


def _analyze_plsql(path: str, content: str) -> tuple[list[_Symbol], list[_Reference], list[str]]:
    matches = list(_PLSQL_SYMBOL.finditer(content))
    symbols: list[_Symbol] = []
    for index, match in enumerate(matches):
        raw_kind, name = match.groups()
        kind = raw_kind.casefold().replace(" ", "_")
        start = _line_number(content, match.start())
        next_start = (
            _line_number(content, matches[index + 1].start()) - 1
            if index + 1 < len(matches)
            else len(content.splitlines())
        )
        parent = next(
            (
                item
                for item in symbols
                if item.kind in {"package", "package_body"} and item.start_line <= start
            ),
            None,
        )
        qualified = (
            f"{parent.name}.{name}" if parent and kind not in {"package", "package_body"} else name
        )
        symbols.append(
            _Symbol(
                _identifier(path, kind, qualified, start),
                name,
                qualified,
                kind,
                start,
                max(start, next_start),
                content.splitlines()[start - 1].strip(),
                parent.symbol_id if parent and parent.name != name else None,
            )
        )
    declaration_offsets = {match.start(2) for match in matches}
    references: list[_Reference] = []
    for match in _PLSQL_CALL.finditer(content):
        name = match.group(1)
        if match.start() in declaration_offsets or name.casefold() in {"if", "loop", "values"}:
            continue
        line = _line_number(content, match.start())
        references.append(
            _Reference(
                _identifier(path, "call", name, line, match.start(), match.end()),
                name,
                "call",
                line,
                1,
            )
        )
    warnings = [] if symbols else ["No PL/SQL structural declarations were recognized."]
    return symbols, references, warnings


def _analyze(payload: dict[str, Any]) -> dict[str, object]:
    path = str(payload["path"])
    language = str(payload["language"]).casefold()
    content = str(payload["content"])
    tree_sitter_result = (
        _tree_sitter_analysis(path, language, content) if language in {"java", "python"} else None
    )
    if tree_sitter_result is not None:
        symbols, references, warnings = tree_sitter_result
        parser_name = f"tree-sitter-{language}"
    elif language == "python":
        symbols, references, warnings = _analyze_python(path, content)
        parser_name = "python-ast"
    elif language == "java":
        symbols, references, warnings = _analyze_java(path, content)
        parser_name = "java-structural"
    elif language == "plsql":
        symbols, references, warnings = _analyze_plsql(path, content)
        parser_name = "plsql-structural"
    else:
        raise ValueError(f"Unsupported structural language: {language}")
    state = "fallback" if warnings and not symbols else "ready"
    return {
        "request_id": payload.get("request_id"),
        "parser_name": parser_name,
        "parser_version": "4",
        "signature_extractor_version": SIGNATURE_EXTRACTOR_VERSION,
        "state": state,
        "symbols": [_symbol_payload(path, item) for item in symbols],
        "references": [_reference_payload(path, item) for item in references],
        "chunks": [_chunk_payload(path, content, item) for item in symbols],
        "warnings": warnings,
    }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        if payload.get("operation") == "health":
            response: dict[str, object] = {"status": "ok", "worker_version": "1"}
        else:
            response = _analyze(payload)
    except Exception as error:  # the parent receives a typed parser crash
        response = {"error": f"{type(error).__name__}: {error}"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
