"""Extract display and canonical symbol signatures."""

from __future__ import annotations

import ast
import re
from typing import Any

SIGNATURE_EXTRACTOR_VERSION = "2"

_WS = re.compile(r"\s+")


def extract_header(
    content: str,
    start_line: int,
    *,
    max_lines: int = 40,
) -> str:
    """Extract a declaration header spanning multiple lines until balanced ``()`` or block start."""
    lines = content.splitlines()
    if start_line < 1 or start_line > len(lines):
        return ""
    collected: list[str] = []
    depth = 0
    started = False
    for index in range(start_line - 1, min(len(lines), start_line - 1 + max_lines)):
        line = lines[index]
        collected.append(line)
        for char in line:
            if char == "(":
                depth += 1
                started = True
            elif char == ")":
                depth = max(0, depth - 1)
        stripped = line.strip()
        if started and depth == 0:
            break
        if not started and (stripped.endswith("{") or stripped.endswith(":")):
            break
    return "\n".join(collected).strip()


def normalize_display(signature: str) -> str:
    return signature.strip()


def canonicalize_java(
    *,
    qualified_name: str,
    display_signature: str,
    return_type: str | None = None,
    parameter_types: list[str] | None = None,
) -> str:
    params = (
        parameter_types
        if parameter_types is not None
        else _parameter_types(_strip_java_annotations(display_signature))
    )
    name = qualified_name or _name_from_display(_strip_java_annotations(display_signature))
    ret = return_type or _java_return_type(_strip_java_annotations(display_signature))
    suffix = f":{ret}" if ret else ""
    return f"{name}({','.join(params)}){suffix}"


def java_signatures_from_node(
    node: Any,
    source: bytes,
    *,
    qualified_name: str,
    content: str,
    start_line: int,
) -> tuple[str, str]:
    """Build display and canonical Java signatures from a Tree-sitter method node."""

    def node_text(value: Any) -> str:
        return source[value.start_byte : value.end_byte].decode("utf-8", errors="replace")

    params_node = node.child_by_field_name("parameters")
    type_node = node.child_by_field_name("type")
    parameter_types = _java_parameter_types_from_node(params_node, source) if params_node else []
    return_type = node_text(type_node).strip() if type_node is not None else None
    if return_type == "void":
        return_type = "void"

    display = normalize_display(extract_header(content, start_line))
    if not display:
        display = node_text(node).splitlines()[0].strip()
    # Prefer a compact declaration without annotation argument noise for display
    # when Tree-sitter formal parameters are available.
    name_node = node.child_by_field_name("name")
    method_name = node_text(name_node) if name_node is not None else qualified_name.split(".")[-1]
    annotations = _java_annotation_prefix(node, source)
    modifiers = _java_modifier_prefix(node, source)
    params_text = node_text(params_node) if params_node is not None else "()"
    type_prefix = f"{return_type} " if return_type else ""
    display = normalize_display(
        f"{annotations}{modifiers}{type_prefix}{method_name}{params_text}".strip()
    )
    canonical = canonicalize_java(
        qualified_name=qualified_name,
        display_signature=display,
        return_type=return_type,
        parameter_types=parameter_types,
    )
    return display, canonical


def canonicalize_python(
    *,
    qualified_name: str,
    args: ast.arguments | None = None,
    returns: ast.AST | None = None,
    display_signature: str | None = None,
) -> str:
    if args is not None:
        params = [_python_arg_type(arg) for arg in [*args.posonlyargs, *args.args]]
        if args.vararg is not None:
            params.append(f"*{_python_arg_type(args.vararg)}")
        params.extend(_python_arg_type(arg) for arg in args.kwonlyargs)
        if args.kwarg is not None:
            params.append(f"**{_python_arg_type(args.kwarg)}")
        ret = _ast_annotation(returns) if returns is not None else None
    else:
        params = _parameter_types(display_signature or "")
        ret = None
    name = qualified_name
    suffix = f":{ret}" if ret else ""
    return f"{name}({','.join(params)}){suffix}"


def canonicalize_plsql(*, qualified_name: str, display_signature: str) -> str:
    params = _parameter_types(display_signature)
    return f"{qualified_name}({','.join(params)})"


def _name_from_display(display_signature: str) -> str:
    match = re.search(r"\b([A-Za-z_$][\w$]*)\s*\(", display_signature)
    return match.group(1) if match else display_signature.splitlines()[0].strip()


def _java_return_type(display_signature: str) -> str | None:
    header = display_signature.split("(", 1)[0]
    tokens = [token for token in re.split(r"\s+", header.strip()) if token]
    skip = {
        "public",
        "protected",
        "private",
        "static",
        "final",
        "abstract",
        "synchronized",
        "native",
        "default",
        "strictfp",
    }
    meaningful = [token for token in tokens if token not in skip and not token.startswith("@")]
    if len(meaningful) >= 2:
        return meaningful[-2]
    return None


def _parameter_types(display_signature: str) -> list[str]:
    start = display_signature.find("(")
    end = display_signature.rfind(")")
    if start < 0 or end <= start:
        return []
    inside = display_signature[start + 1 : end].strip()
    if not inside:
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in inside:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    types: list[str] = []
    for part in parts:
        cleaned = _WS.sub(" ", part).strip()
        if not cleaned:
            continue
        # Drop leading parameter annotations such as @NotNull.
        cleaned = _strip_java_annotations(cleaned).strip()
        if not cleaned:
            continue
        tokens = cleaned.replace("...", " ...").split()
        if len(tokens) >= 2:
            types.append(tokens[-2].rstrip(","))
        else:
            types.append(tokens[0])
    return types


def _strip_java_annotations(text: str) -> str:
    """Remove ``@Annotation`` and ``@Annotation(...)`` segments from Java source text."""
    result: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "@":
            result.append(char)
            index += 1
            continue
        index += 1
        while index < length and (text[index].isalnum() or text[index] in "._"):
            index += 1
        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] == "(":
            depth = 0
            while index < length:
                current = text[index]
                if current == "(":
                    depth += 1
                elif current == ")":
                    depth -= 1
                    index += 1
                    if depth == 0:
                        break
                    continue
                index += 1
        while index < length and text[index].isspace():
            index += 1
    return "".join(result)


def _java_parameter_types_from_node(params_node: Any, source: bytes) -> list[str]:
    types: list[str] = []

    def node_text(value: Any) -> str:
        return source[value.start_byte : value.end_byte].decode("utf-8", errors="replace")

    for child in params_node.children:
        if child.type not in {"formal_parameter", "spread_parameter", "receiver_parameter"}:
            continue
        type_node = child.child_by_field_name("type")
        if type_node is None:
            # Fall back to identifier-bearing tokens excluding the parameter name.
            pieces = [
                node_text(item)
                for item in child.children
                if item.type not in {"identifier", ",", "annotation", "marker_annotation"}
                and not item.type.endswith("annotation")
            ]
            cleaned = _WS.sub(" ", " ".join(pieces)).strip()
            if cleaned:
                types.append(cleaned.rstrip("...").strip() or cleaned)
            continue
        type_text = node_text(type_node).strip()
        if child.type == "spread_parameter" and not type_text.endswith("..."):
            type_text = f"{type_text}..."
        types.append(type_text)
    return types


def _java_annotation_prefix(node: Any, source: bytes) -> str:
    pieces: list[str] = []

    def node_text(value: Any) -> str:
        return source[value.start_byte : value.end_byte].decode("utf-8", errors="replace")

    for child in node.children:
        if child.type in {"modifiers"}:
            for item in child.children:
                if "annotation" in item.type:
                    pieces.append(node_text(item))
        elif "annotation" in child.type:
            pieces.append(node_text(child))
    if not pieces:
        return ""
    return " ".join(pieces) + " "


def _java_modifier_prefix(node: Any, source: bytes) -> str:
    modifiers = {
        "public",
        "protected",
        "private",
        "static",
        "final",
        "abstract",
        "synchronized",
        "native",
        "default",
        "strictfp",
    }
    pieces: list[str] = []

    def node_text(value: Any) -> str:
        return source[value.start_byte : value.end_byte].decode("utf-8", errors="replace")

    for child in node.children:
        if child.type == "modifiers":
            for item in child.children:
                text = node_text(item).strip()
                if text in modifiers:
                    pieces.append(text)
        elif child.type in modifiers:
            pieces.append(node_text(child).strip())
    if not pieces:
        return ""
    return " ".join(pieces) + " "


def _python_arg_type(arg: ast.arg) -> str:
    annotation = _ast_annotation(arg.annotation)
    return annotation or arg.arg


def _ast_annotation(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None
