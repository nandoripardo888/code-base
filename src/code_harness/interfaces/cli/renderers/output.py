import json
import sys
from dataclasses import is_dataclass
from enum import StrEnum
from typing import Any

import typer

from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead
from code_harness.domain.models.context import ContextBundle
from code_harness.domain.models.file_listing import FileListingPage
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.repository_map import RepositoryDirectory, RepositoryMap
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.serialization import serialize_error, to_primitive

__all__ = ["OutputFormat", "render_error", "render_value", "to_primitive"]


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


def _render_item(value: Any) -> str:
    if isinstance(value, SourceFile):
        language = value.language or "text"
        return f"{value.path}\t{value.size_bytes} bytes\t{language}"
    if isinstance(value, FileListingPage):
        lines = [_render_item(item) for item in value.items]
        footer = f"sort={value.sort}:{value.sort_direction}"
        if value.total_count is not None:
            footer += f"; total={value.total_count}"
        if value.next_cursor:
            footer += f"; next_cursor={value.next_cursor}"
        lines.append(footer)
        return "\n".join(lines)
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
    if isinstance(value, SourceRead):
        body = value.snippet.content
        if value.numbered_lines:
            body = "\n".join(f"{number:>6}|{text}" for number, text in value.numbered_lines)
        footer = ""
        if value.truncation and value.truncation.truncated:
            footer = (
                f"\n[truncated reason={value.truncation.reason}; "
                f"next_start_line={value.truncation.next_start_line}; "
                f"total_lines={value.truncation.total_lines}]"
            )
        return f"{body}{footer}"
    if isinstance(value, CodeSnippet):
        return value.content
    if isinstance(value, StructuralSearchResult):
        item = value.symbol or value.reference
        if item is None:
            return value.content or ""
        location = item.location
        if value.symbol is not None:
            signature = value.symbol.signature or ""
            heading = (
                f"{location.path}:{location.start_line}-{location.end_line} "
                f"[{value.symbol.kind} {value.symbol.qualified_name or value.symbol.name}]"
            )
            if not value.content_included:
                return f"{heading}\n{signature}".rstrip()
        else:
            reference = value.reference
            if reference is None:
                return value.content or ""
            heading = (
                f"{location.path}:{location.start_line}-{location.end_line} "
                f"[{reference.kind} -> {reference.target_name}]"
            )
        body = (value.content or "").rstrip()
        return f"{heading}\n{body}" if body else heading
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
        from code_harness.domain.models.tool_result import warning_message

        for warning in value.warnings:
            _echo(f"warning: {warning_message(warning)}", err=True)


def render_error(error: CodeHarnessError, output: OutputFormat) -> None:
    payload = serialize_error(error)
    if output in (OutputFormat.JSON, OutputFormat.JSONL):
        _echo(_json(payload), err=True)
    else:
        _echo(f"error [{error.code.value}]: {error.message}", err=True)
