import json
import sys
from dataclasses import fields, is_dataclass
from enum import StrEnum
from typing import Any

import typer

from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.context import ContextBundle
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.repository_map import RepositoryDirectory, RepositoryMap
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult


class OutputFormat(StrEnum):
    TEXT = "text"
    TABLE = "table"
    JSON = "json"
    JSONL = "jsonl"
    LLM = "llm"


def _json(value: Any, *, indent: int | None = None) -> str:
    # Machine-readable output must remain printable even when a Windows console
    # uses cp1252. JSON escapes preserve the exact Unicode value for consumers.
    return json.dumps(value, ensure_ascii=True, indent=indent)


def _echo(value: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    encoding = stream.encoding or "utf-8"
    printable = value.encode(encoding, errors="backslashreplace").decode(encoding)
    typer.echo(printable, err=err)


def to_primitive(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    return value


def _render_item(value: Any) -> str:
    if isinstance(value, SourceFile):
        language = value.language or "text"
        return f"{value.path}\t{value.size_bytes} bytes\t{language}"
    if isinstance(value, FileMatch):
        return f"{value.source_file.path}\t{value.score:.2f}\t{value.reason}"
    if isinstance(value, SearchHit):
        location = value.snippet.location
        heading = (
            f"{location.path}:{location.start_line}-{location.end_line} "
            f"[{value.match_type.value} {value.score:.2f}]"
        )
        return f"{heading}\n{value.snippet.content.rstrip()}"
    if isinstance(value, HybridSearchHit):
        location = value.snippet.location
        strategies = ",".join(item.match_type.value for item in value.evidence)
        heading = (
            f"{location.path}:{location.start_line}-{location.end_line} "
            f"[hybrid {value.score:.2f}; {strategies}]"
        )
        return f"{heading}\n{value.snippet.content.rstrip()}\nreason: {value.reason}"
    if isinstance(value, ContextBundle):
        sections: list[str] = []
        for context_item in value.snippets:
            location = context_item.snippet.location
            relation = f"; relation={context_item.relation}" if context_item.relation else ""
            sections.append(
                f"[{context_item.role}{relation}; depth={context_item.depth}] "
                f"{location.path}:{location.start_line}-{location.end_line}\n"
                f"{context_item.snippet.content.rstrip()}"
            )
        footer = (
            f"estimated_tokens={value.estimated_tokens}/{value.available_tokens}; "
            f"omitted_results={value.omitted_results}"
        )
        return "\n\n".join((*sections, footer))
    if isinstance(value, RepositoryMap):
        lines = _render_directory(value.root)
        lines.append(
            f"files={value.included_files}/{value.total_files}; omitted={value.omitted_files}"
        )
        return "\n".join(lines)
    if isinstance(value, CodeSnippet):
        return value.content
    if isinstance(value, StructuralSearchResult):
        item = value.symbol or value.reference
        if item is None:
            return value.content
        location = item.location
        if value.symbol is not None:
            heading = (
                f"{location.path}:{location.start_line}-{location.end_line} "
                f"[{value.symbol.kind} {value.symbol.qualified_name or value.symbol.name}]"
            )
        else:
            reference = value.reference
            if reference is None:
                return value.content
            heading = (
                f"{location.path}:{location.start_line}-{location.end_line} "
                f"[{reference.kind} -> {reference.target_name}]"
            )
        return f"{heading}\n{value.content.rstrip()}"
    if is_dataclass(value):
        return _json(to_primitive(value), indent=2)
    return str(value)


def _render_directory(directory: RepositoryDirectory, prefix: str = "") -> list[str]:
    lines: list[str] = []
    if directory.path:
        lines.append(f"{prefix}{directory.name}/")
        prefix += "  "
    for child in directory.directories:
        lines.extend(_render_directory(child, prefix))
    for source in directory.files:
        language = source.language or "text"
        lines.append(f"{prefix}{source.name} [{language}]")
        for symbol in source.symbols:
            lines.append(
                f"{prefix}  {symbol.kind} {symbol.qualified_name or symbol.name} "
                f"(line {symbol.start_line})"
            )
    return lines


def render_value(value: Any, output: OutputFormat) -> None:
    if output is OutputFormat.JSON:
        _echo(_json(to_primitive(value), indent=2))
        return
    data = value.data if isinstance(value, ToolResult) else value
    if output is OutputFormat.JSONL:
        items = data if isinstance(data, tuple) else (data,)
        for item in items:
            _echo(_json(to_primitive(item)))
        return
    if isinstance(data, tuple):
        _echo("\n\n".join(_render_item(item) for item in data))
    else:
        _echo(_render_item(data))
    if isinstance(value, ToolResult) and value.warnings:
        for warning in value.warnings:
            _echo(f"warning: {warning}", err=True)


def render_error(error: CodeHarnessError, output: OutputFormat) -> None:
    payload = {
        "error": {
            "code": error.code.value,
            "message": error.message,
            "details": error.details,
        }
    }
    if output in (OutputFormat.JSON, OutputFormat.JSONL):
        _echo(_json(payload), err=True)
    else:
        _echo(f"error [{error.code.value}]: {error.message}", err=True)
