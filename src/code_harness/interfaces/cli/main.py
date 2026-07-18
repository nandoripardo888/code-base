from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from code_harness.application.dto.requests import (
    ListFilesRequest,
    ReadFileRequest,
    ReadRangeRequest,
    SearchFilesRequest,
    SearchRegexRequest,
    SearchTextRequest,
)
from code_harness.bootstrap.container import ApplicationContainer, build_container
from code_harness.bootstrap.project_registry import register_active_project, resolve_active_project
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ErrorCode
from code_harness.domain.errors import CodeHarnessError, InvalidQueryError
from code_harness.interfaces.cli.renderers import OutputFormat, render_error, render_value
from code_harness.version import __version__

app = typer.Typer(
    name="code-harness",
    help="Local-first, traceable code retrieval for humans and LLMs.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
files_app = typer.Typer(help="Discover and locate files.", no_args_is_help=True)
search_app = typer.Typer(help="Search source content.", no_args_is_help=True)
app.add_typer(files_app, name="files")
app.add_typer(search_app, name="search")


@dataclass(slots=True)
class CliState:
    project: Path | None
    output: OutputFormat
    _container: ApplicationContainer | None = None

    def container(self) -> ApplicationContainer:
        if self._container is None:
            root = resolve_active_project(self.project)
            self._container = build_container(Settings.for_root(root))
        return self._container


def _exit_code(error: CodeHarnessError) -> int:
    if error.code is ErrorCode.PROJECT_NOT_FOUND or error.code is ErrorCode.FILE_NOT_FOUND:
        return 3
    if error.code is ErrorCode.RIPGREP_UNAVAILABLE:
        return 4
    if error.code in (ErrorCode.INDEX_NOT_READY, ErrorCode.INDEX_CORRUPTED):
        return 5
    if error.code in (ErrorCode.INVALID_QUERY, ErrorCode.PATH_OUTSIDE_PROJECT):
        return 2
    return 6


def _execute(state: CliState, operation: Any) -> None:
    try:
        result = operation()
    except ValueError as error:
        typed_error = InvalidQueryError(str(error))
        render_error(typed_error, state.output)
        raise typer.Exit(2) from error
    except CodeHarnessError as error:
        render_error(error, state.output)
        raise typer.Exit(_exit_code(error)) from error
    render_value(result, state.output)


@app.callback()
def root_options(
    ctx: typer.Context,
    project: Annotated[
        Path | None,
        typer.Option("--project", "-p", help="Project root; overrides the active project."),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", case_sensitive=False, help="Output renderer."),
    ] = OutputFormat.TEXT,
    version: Annotated[
        bool,
        typer.Option("--version", is_eager=True, help="Show the installed version and exit."),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    ctx.obj = CliState(project, output)


@app.command("init")
def initialize(
    ctx: typer.Context,
    root: Annotated[Path, typer.Argument(help="Repository root to register as active.")],
) -> None:
    state: CliState = ctx.obj
    _execute(state, lambda: {"root": str(register_active_project(root)), "active": True})


@files_app.command("list")
def list_files(
    ctx: typer.Context,
    include: Annotated[list[str] | None, typer.Option("--include", help="Include glob.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Exclude glob.")] = None,
    max_results: Annotated[int, typer.Option("--max-results", min=1)] = 10_000,
) -> None:
    state: CliState = ctx.obj
    request = ListFilesRequest(tuple(include or ()), tuple(exclude or ()), max_results)
    _execute(state, lambda: state.container().list_files.execute(request))


@files_app.command("search")
def search_files(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="File name or path fragment.")],
    include: Annotated[list[str] | None, typer.Option("--include", help="Include glob.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Exclude glob.")] = None,
    max_results: Annotated[int, typer.Option("--max-results", min=1)] = 50,
    case_sensitive: Annotated[bool, typer.Option("--case-sensitive")] = False,
) -> None:
    state: CliState = ctx.obj
    request = SearchFilesRequest(
        query,
        tuple(include or ()),
        tuple(exclude or ()),
        max_results,
        case_sensitive,
    )
    _execute(state, lambda: state.container().search_files.execute(request))


def _search(
    ctx: typer.Context,
    query: str,
    *,
    regex: bool,
    include: list[str] | None,
    exclude: list[str] | None,
    max_results: int,
    context_lines: int,
    case_sensitive: bool,
    timeout_seconds: float,
) -> None:
    state: CliState = ctx.obj
    arguments = (
        query,
        tuple(include or ()),
        tuple(exclude or ()),
        max_results,
        context_lines,
        case_sensitive,
        timeout_seconds,
    )
    if regex:
        regex_request = SearchRegexRequest(*arguments)
        _execute(state, lambda: state.container().search_regex.execute(regex_request))
    else:
        text_request = SearchTextRequest(*arguments)
        _execute(state, lambda: state.container().search_text.execute(text_request))


@search_app.command("text")
def search_text(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Literal text to find.")],
    include: Annotated[list[str] | None, typer.Option("--include", help="Include glob.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Exclude glob.")] = None,
    max_results: Annotated[int, typer.Option("--max-results", min=1)] = 50,
    context_lines: Annotated[int, typer.Option("--context-lines", min=0)] = 0,
    case_sensitive: Annotated[bool, typer.Option("--case-sensitive")] = False,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=0.1)] = 10.0,
) -> None:
    _search(
        ctx,
        query,
        regex=False,
        include=include,
        exclude=exclude,
        max_results=max_results,
        context_lines=context_lines,
        case_sensitive=case_sensitive,
        timeout_seconds=timeout_seconds,
    )


@search_app.command("regex")
def search_regex(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Ripgrep regular expression.")],
    include: Annotated[list[str] | None, typer.Option("--include", help="Include glob.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Exclude glob.")] = None,
    max_results: Annotated[int, typer.Option("--max-results", min=1)] = 50,
    context_lines: Annotated[int, typer.Option("--context-lines", min=0)] = 0,
    case_sensitive: Annotated[bool, typer.Option("--case-sensitive")] = False,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=0.1)] = 10.0,
) -> None:
    _search(
        ctx,
        query,
        regex=True,
        include=include,
        exclude=exclude,
        max_results=max_results,
        context_lines=context_lines,
        case_sensitive=case_sensitive,
        timeout_seconds=timeout_seconds,
    )


def _parse_line_range(value: str) -> tuple[int, int]:
    parts = value.split(":", maxsplit=1)
    if len(parts) != 2:
        raise InvalidQueryError("--lines must use START:END format.", lines=value)
    try:
        start_line, end_line = (int(part) for part in parts)
    except ValueError as error:
        raise InvalidQueryError(
            "--lines must contain integer line numbers.", lines=value
        ) from error
    if start_line < 1 or end_line < start_line:
        raise InvalidQueryError("--lines must be a positive inclusive range.", lines=value)
    return start_line, end_line


@app.command("read")
def read_source(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Relative source path.")],
    lines: Annotated[str | None, typer.Option("--lines", help="Inclusive START:END range.")] = None,
    max_chars: Annotated[int, typer.Option("--max-chars", min=1)] = 200_000,
    max_lines: Annotated[int, typer.Option("--max-lines", min=1)] = 5_000,
) -> None:
    state: CliState = ctx.obj
    if lines is None:
        request = ReadFileRequest(path, max_chars, max_lines)
        _execute(state, lambda: state.container().read_file.execute(request))
        return
    try:
        start_line, end_line = _parse_line_range(lines)
    except CodeHarnessError as error:
        render_error(error, state.output)
        raise typer.Exit(_exit_code(error)) from error
    request_range = ReadRangeRequest(path, start_line, end_line, max_chars)
    _execute(state, lambda: state.container().read_range.execute(request_range))


def main() -> None:
    app()
