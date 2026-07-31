"""Thin CLI over the same tools, for manual debugging."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from code_harness import tools
from code_harness.errors import HarnessError
from code_harness.session import Session
from code_harness.version import __version__

app = typer.Typer(add_completion=False, help="Cursor-like local tools for one project.")
mcp_app = typer.Typer(help="MCP server commands.")
app.add_typer(mcp_app, name="mcp")

_ROOT = typer.Option(None, "--project", "-p", help="Project root; defaults to the current dir.")


def _run(project: Path | None, action: Callable[[Session], Any]) -> None:
    session = Session.create(project)
    try:
        result = action(session)
        typer.echo(json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result)
    except HarnessError as error:
        typer.echo(error.render(), err=True)
        raise typer.Exit(code=1) from error
    finally:
        session.shutdown()


def _serve(project: Path | None) -> None:
    from code_harness.mcp.server import run_server

    run_server(project)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def serve(project: Path | None = _ROOT) -> None:
    """Run the MCP server over stdio."""
    _serve(project)


@mcp_app.command("serve")
def mcp_serve(project: Path | None = _ROOT) -> None:
    """Run the MCP server over stdio (alias for ``serve``)."""
    _serve(project)


@app.command()
def shell(
    command: str,
    working_directory: str | None = typer.Option(None, "--cwd"),
    block_until_ms: int = typer.Option(tools.DEFAULT_BLOCK_UNTIL_MS, "--block-until-ms"),
    shell_name: str = typer.Option("auto", "--shell"),
    project: Path | None = _ROOT,
) -> None:
    """Run a shell command."""
    _run(
        project,
        lambda session: tools.shell(
            session.guard,
            session.jobs,
            command=command,
            working_directory=working_directory,
            block_until_ms=block_until_ms,
            shell=shell_name,
        ),
    )


@app.command()
def grep(
    pattern: str,
    path: str | None = typer.Option(None, "--path"),
    glob: str | None = typer.Option(None, "--glob"),
    file_type: str | None = typer.Option(None, "--type"),
    output_mode: str = typer.Option("content", "--output-mode"),
    case_insensitive: bool = typer.Option(False, "--case-insensitive", "-i"),
    context_lines: int | None = typer.Option(None, "--context", "-C"),
    multiline: bool = typer.Option(False, "--multiline"),
    head_limit: int | None = typer.Option(None, "--head-limit"),
    offset: int | None = typer.Option(None, "--offset"),
    project: Path | None = _ROOT,
) -> None:
    """Search file contents with a regular expression."""
    _run(
        project,
        lambda session: tools.grep(
            session.guard,
            pattern=pattern,
            path=path,
            glob=glob,
            file_type=file_type,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context_lines=context_lines,
            multiline=multiline,
            head_limit=head_limit,
            offset=offset,
        ),
    )


@app.command(name="glob")
def glob_command(
    glob_pattern: str,
    target_directory: str | None = typer.Option(None, "--dir"),
    project: Path | None = _ROOT,
) -> None:
    """Find files matching a glob pattern."""
    _run(
        project,
        lambda session: tools.glob(
            session.guard,
            glob_pattern=glob_pattern,
            target_directory=target_directory,
        ),
    )


@app.command()
def read(
    path: str,
    offset: int | None = typer.Option(None, "--offset"),
    limit: int | None = typer.Option(None, "--limit"),
    project: Path | None = _ROOT,
) -> None:
    """Read a file as numbered lines."""

    def action(session: Session) -> str:
        result = tools.read(session.guard, path=path, offset=offset, limit=limit)
        if isinstance(result, tools.ImageResult):
            return f"<image {result.mime_type}, {len(result.data)} bytes: {result.path}>"
        return result

    _run(project, action)


@app.command()
def write(path: str, contents: str, project: Path | None = _ROOT) -> None:
    """Create or overwrite a file."""
    _run(project, lambda session: tools.write(session.guard, path=path, contents=contents))


@app.command(name="str-replace")
def str_replace_command(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = typer.Option(False, "--replace-all"),
    ignore_line_endings: bool = typer.Option(
        True,
        "--ignore-line-endings/--exact-line-endings",
    ),
    expected_occurrences: int | None = typer.Option(None, "--expected-occurrences"),
    expected_sha256: str | None = typer.Option(None, "--expected-sha256"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    project: Path | None = _ROOT,
) -> None:
    """Replace text, tolerating LF/CRLF differences by default."""
    _run(
        project,
        lambda session: tools.str_replace(
            session.guard,
            path=path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            ignore_line_endings=ignore_line_endings,
            expected_occurrences=expected_occurrences,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
        ),
    )


@app.command(name="apply-patch")
def apply_patch_command(
    patch_file: Path,
    dry_run: bool = typer.Option(False, "--dry-run"),
    project: Path | None = _ROOT,
) -> None:
    """Apply a unified diff from a file using Git."""
    patch = patch_file.read_text(encoding="utf-8")
    _run(
        project,
        lambda session: tools.apply_patch(
            session.guard,
            session.history,
            patch=patch,
            dry_run=dry_run,
        ),
    )


@app.command(name="rollback-patch")
def rollback_patch_command(
    transaction_id: str,
    force: bool = typer.Option(False, "--force"),
    project: Path | None = _ROOT,
) -> None:
    """Restore a successful patch transaction from its byte snapshots."""
    _run(
        project,
        lambda session: tools.rollback_patch(
            session.history,
            transaction_id=transaction_id,
            force=force,
        ),
    )


@app.command()
def delete(path: str, project: Path | None = _ROOT) -> None:
    """Delete a file."""
    _run(project, lambda session: tools.delete(session.guard, path=path))


@app.command()
def review(
    transaction_id: str = typer.Argument("latest"),
    no_open: bool = typer.Option(False, "--no-open"),
    project: Path | None = _ROOT,
) -> None:
    """Open a local patch review and keep it available until interrupted."""
    session = Session.create(project)
    try:
        result = session.reviews.open(transaction_id, open_browser=not no_open)
        typer.echo(json.dumps(result, ensure_ascii=False))
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        return
    except HarnessError as error:
        typer.echo(error.render(), err=True)
        raise typer.Exit(code=1) from error
    finally:
        session.shutdown()


def main() -> None:
    app()
