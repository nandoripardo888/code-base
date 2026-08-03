"""Optional Tree-sitter parsing for structural symbols and references."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from code_harness.errors import ParserSupportUnavailableError, UnsupportedReferenceLanguageError
from code_harness.symbols.models import Reference, ReferenceKind, Symbol, SymbolKind

SUPPORTED_EXTENSIONS = frozenset({".py", ".java", ".js", ".jsx", ".ts", ".tsx"})

_SPEC_BY_EXTENSION: dict[str, tuple[str, str, str]] = {
    ".py": ("python", "tree_sitter_python", "language"),
    ".java": ("java", "tree_sitter_java", "language"),
    ".js": ("javascript", "tree_sitter_javascript", "language"),
    ".jsx": ("javascript", "tree_sitter_javascript", "language"),
    ".ts": ("typescript", "tree_sitter_typescript", "language_typescript"),
    ".tsx": ("typescript", "tree_sitter_typescript", "language_tsx"),
}

_SYMBOL_NODES: dict[str, dict[str, SymbolKind]] = {
    "python": {
        "class_definition": "class",
        "function_definition": "function",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "class",
        "method_declaration": "method",
        "constructor_declaration": "method",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
    },
    "typescript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
}

_IDENTIFIER_NODES = frozenset(
    {
        "identifier",
        "property_identifier",
        "type_identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
    }
)
_IMPORT_NODES = frozenset(
    {
        "import_declaration",
        "import_statement",
        "import_from_statement",
        "export_statement",
    }
)
_CALL_NODES = frozenset({"call", "call_expression", "method_invocation"})
_NEW_NODES = frozenset({"new_expression", "object_creation_expression"})
_IMPLEMENTATION_CONTEXTS = frozenset({"implements_clause", "super_interfaces"})
_TYPE_CONTEXTS = frozenset(
    {
        "type_annotation",
        "type_arguments",
        "type_parameter",
        "generic_type",
        "superclass",
        "implements_clause",
        "extends_type_clause",
    }
)
_DEFINITION_PARENTS = frozenset(
    {
        "class_definition",
        "function_definition",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "method_declaration",
        "constructor_declaration",
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "type_alias_declaration",
        "variable_declarator",
        "formal_parameter",
        "required_parameter",
        "optional_parameter",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    symbols: tuple[Symbol, ...]
    references: tuple[Reference, ...]
    has_errors: bool


class TreeSitterRegistry:
    def __init__(self) -> None:
        self._runtime: Any | None = None
        self._parsers: dict[str, Any] = {}

    def require_runtime(self) -> None:
        self._load_runtime()

    def supports(self, path: str) -> bool:
        return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

    def parse(self, path: str, source_text: str) -> ParsedFile:
        extension = Path(path).suffix.lower()
        spec = _SPEC_BY_EXTENSION.get(extension)
        if spec is None:
            raise UnsupportedReferenceLanguageError(path)
        language_name, module_name, factory_name = spec
        parser = self._parser(extension, module_name, factory_name)
        source = source_text.encode("utf-8")
        root = parser.parse(source).root_node
        lines = source_text.splitlines()
        symbols: list[Symbol] = []
        references: list[Reference] = []

        def visit(
            node: Any,
            *,
            field_name: str | None,
            ancestors: tuple[tuple[Any, str | None], ...],
            container: str | None,
            container_kind: SymbolKind | None,
        ) -> None:
            symbol_kind = _SYMBOL_NODES[language_name].get(node.type)
            next_container = container
            next_container_kind = container_kind
            if symbol_kind is not None:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(source, name_node)
                    effective_kind = symbol_kind
                    if (
                        language_name == "python"
                        and symbol_kind == "function"
                        and container_kind == "class"
                    ):
                        effective_kind = "method"
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=effective_kind,
                            path=path,
                            line=_point(name_node.start_point, 0) + 1,
                            language=language_name,
                            container=container,
                        )
                    )
                    next_container = f"{container}.{name}" if container else name
                    next_container_kind = effective_kind

            if node.type in _IDENTIFIER_NODES:
                name = _node_text(source, node)
                kind = _reference_kind(node, field_name=field_name, ancestors=ancestors)
                line = _point(node.start_point, 0) + 1
                column = _point(node.start_point, 1) + 1
                excerpt = lines[line - 1].strip() if 1 <= line <= len(lines) else name
                references.append(
                    Reference(
                        name=name,
                        kind=kind,
                        path=path,
                        line=line,
                        column=column,
                        language=language_name,
                        excerpt=excerpt,
                        container=container,
                    )
                )

            next_ancestors = (*ancestors, (node, field_name))
            for index, child in enumerate(node.children):
                child_field = node.field_name_for_child(index)
                child_container = (
                    container
                    if symbol_kind is not None and child_field == "name"
                    else next_container
                )
                visit(
                    child,
                    field_name=child_field,
                    ancestors=next_ancestors,
                    container=child_container,
                    container_kind=next_container_kind,
                )

        visit(
            root,
            field_name=None,
            ancestors=(),
            container=None,
            container_kind=None,
        )
        return ParsedFile(tuple(symbols), tuple(references), bool(root.has_error))

    def _load_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime
        try:
            self._runtime = importlib.import_module("tree_sitter")
        except ImportError as error:
            raise ParserSupportUnavailableError("Tree-sitter is not installed.") from error
        return self._runtime

    def _parser(self, extension: str, module_name: str, factory_name: str) -> Any:
        cached = self._parsers.get(extension)
        if cached is not None:
            return cached
        runtime = self._load_runtime()
        try:
            grammar = importlib.import_module(module_name)
        except ImportError as error:
            raise ParserSupportUnavailableError(f"Missing parser package {module_name}.") from error
        language = runtime.Language(getattr(grammar, factory_name)())
        parser = runtime.Parser(language)
        self._parsers[extension] = parser
        return parser


class ParserSymbolExtractor:
    """Merge parser symbols with an existing best-effort extractor."""

    def __init__(
        self,
        *,
        language: str,
        extensions: frozenset[str],
        fallback: Any,
        registry: TreeSitterRegistry,
    ) -> None:
        self.language = language
        self.extensions = extensions
        self._fallback = fallback
        self._registry = registry

    def extract(self, path: str, source: str) -> list[Symbol]:
        fallback_symbols = cast(list[Symbol], self._fallback.extract(path, source))
        try:
            parsed = self._registry.parse(path, source)
        except Exception:
            return fallback_symbols

        parsed_symbols = list(parsed.symbols)
        # The Java parser covers every declaration kind supported by the model.
        # Its generic textual fallback is intentionally broad and can mistake
        # statements such as ``throw new DataException(...)`` for functions.
        if self.language == "java" and (parsed_symbols or not parsed.has_errors):
            return parsed_symbols

        # Parser output wins when both extractors report the same source token.
        # Kinds can legitimately differ (for example JavaScript/Python methods
        # are "method" syntactically but may be "function" in a fallback).
        seen = {(item.name, item.line) for item in parsed_symbols}
        parsed_symbols.extend(
            item for item in fallback_symbols if (item.name, item.line) not in seen
        )
        return parsed_symbols


def _reference_kind(
    node: Any,
    *,
    field_name: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> ReferenceKind:
    if not ancestors:
        return "usage"
    parent, _ = ancestors[-1]
    ancestor_types = {item.type for item, _ in ancestors}

    if parent.type in _DEFINITION_PARENTS and field_name in {"name", "pattern"}:
        return "definition"
    if ancestor_types & _IMPORT_NODES:
        return "import"
    if ancestor_types & _IMPLEMENTATION_CONTEXTS:
        return "implementation"
    if _is_called_identifier(parent, field_name=field_name, ancestors=ancestors):
        return "call"
    if _is_instantiated_identifier(field_name=field_name, ancestors=ancestors):
        return "instantiation"
    if node.type == "type_identifier" or ancestor_types & _TYPE_CONTEXTS:
        return "type_use"
    return "usage"


def _is_called_identifier(
    parent: Any,
    *,
    field_name: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> bool:
    if parent.type in _CALL_NODES and field_name in {"function", "name"}:
        return True
    if len(ancestors) < 2:
        return False
    grandparent, _ = ancestors[-2]
    parent_field = ancestors[-1][1]
    return (
        grandparent.type in _CALL_NODES
        and parent_field in {"function", "name"}
        and field_name in {"attribute", "property", "name"}
    )


def _is_instantiated_identifier(
    *,
    field_name: str | None,
    ancestors: tuple[tuple[Any, str | None], ...],
) -> bool:
    """Return true only for the constructor branch of a new expression.

    Looking only for any ``new`` ancestor incorrectly labels a chained method,
    such as ``new Service().getStatus()``, as an instantiation.
    """
    constructor_fields = {"constructor", "type", "class"}
    for index, (ancestor, _) in enumerate(ancestors):
        if ancestor.type not in _NEW_NODES:
            continue
        branch_field = (
            field_name if index == len(ancestors) - 1 else ancestors[index + 1][1]
        )
        return branch_field in constructor_fields
    return False


def _node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _point(point: Any, index: int) -> int:
    try:
        return int(point[index])
    except TypeError:
        return int(point.row if index == 0 else point.column)
