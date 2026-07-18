import json
from dataclasses import fields, is_dataclass
from enum import StrEnum
from typing import Any

import typer

from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.tool_result import ToolResult


class OutputFormat(StrEnum):
    TEXT = "text"
    TABLE = "table"
    JSON = "json"
    JSONL = "jsonl"
    LLM = "llm"


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
    if isinstance(value, CodeSnippet):
        return value.content
    if is_dataclass(value):
        return json.dumps(to_primitive(value), ensure_ascii=False, indent=2)
    return str(value)


def render_value(value: Any, output: OutputFormat) -> None:
    if output is OutputFormat.JSON:
        typer.echo(json.dumps(to_primitive(value), ensure_ascii=False, indent=2))
        return
    data = value.data if isinstance(value, ToolResult) else value
    if output is OutputFormat.JSONL:
        items = data if isinstance(data, tuple) else (data,)
        for item in items:
            typer.echo(json.dumps(to_primitive(item), ensure_ascii=False))
        return
    if isinstance(data, tuple):
        typer.echo("\n\n".join(_render_item(item) for item in data))
    else:
        typer.echo(_render_item(data))
    if isinstance(value, ToolResult) and value.warnings:
        for warning in value.warnings:
            typer.echo(f"warning: {warning}", err=True)


def render_error(error: CodeHarnessError, output: OutputFormat) -> None:
    payload = {
        "error": {
            "code": error.code.value,
            "message": error.message,
            "details": error.details,
        }
    }
    if output in (OutputFormat.JSON, OutputFormat.JSONL):
        typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
    else:
        typer.echo(f"error [{error.code.value}]: {error.message}", err=True)
