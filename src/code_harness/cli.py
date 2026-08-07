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
_MCP_TRANSPORT = typer.Option(
    None,
    "--transport",
    help="MCP transport: stdio or streamable-http. Defaults to env or stdio.",
)
_MCP_HOST = typer.Option(None, "--host", help="HTTP listen host.")
_MCP_PORT = typer.Option(None, "--port", help="HTTP listen port.")
_MCP_PATH = typer.Option(None, "--path", help="Streamable HTTP endpoint path.")
_MCP_API_KEY = typer.Option(None, "--api-key", help="API key required by the HTTP endpoint.")
_MCP_NO_API_KEY = typer.Option(
    False,
    "--no-api-key",
    help="Disable API-key auth, overriding CODE_HARNESS_MCP_API_KEY.",
)
_MCP_PUBLIC_URL = typer.Option(
    None,
    "--public-url",
    help="Public connector URL, used to allow tunnel Host/Origin values.",
)
_MCP_ALLOWED_HOSTS = typer.Option(None, "--allowed-host", help="Allowed HTTP Host; repeatable.")
_MCP_ALLOWED_ORIGINS = typer.Option(
    None,
    "--allowed-origin",
    help="Allowed HTTP Origin; repeatable.",
)
_MCP_DISABLE_DNS_REBINDING = typer.Option(
    None,
    "--disable-dns-rebinding/--enable-dns-rebinding",
    help="Override FastMCP DNS-rebinding protection.",
)
_GLOB_FILTER = typer.Option(
    None,
    "--glob",
    help="File glob filter; repeatable. Brace patterns like *.{py,md} are expanded.",
)
_EXCLUDE_FILTER = typer.Option(
    None,
    "--exclude",
    help="File glob to exclude; repeatable. Explicit exclusions always apply.",
)
_REFERENCE_KIND_FILTER = typer.Option(
    None,
    "--reference-kind",
    help="Reference category to include; repeatable or comma-separated.",
)
_EXCLUDE_REFERENCE_KIND_FILTER = typer.Option(
    None,
    "--exclude-reference-kind",
    help="Reference category to omit; repeatable or comma-separated.",
)
_GLOB_PATTERNS = typer.Argument(
    ...,
    help="Glob pattern(s); braces like *.{py,md} expand. Pass multiple patterns as args.",
)


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


def _serve(
    project: Path | None,
    *,
    transport: str | None,
    host: str | None,
    port: int | None,
    path: str | None,
    api_key: str | None,
    no_api_key: bool,
    public_url: str | None,
    allowed_hosts: list[str] | None,
    allowed_origins: list[str] | None,
    disable_dns_rebinding: bool | None,
) -> None:
    from code_harness.mcp.server import run_server

    try:
        run_server(
            project,
            transport=transport,
            host=host,
            port=port,
            path=path,
            api_key=api_key,
            no_api_key=no_api_key,
            public_url=public_url,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            disable_dns_rebinding=disable_dns_rebinding,
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def serve(
    project: Path | None = _ROOT,
    transport: str | None = _MCP_TRANSPORT,
    host: str | None = _MCP_HOST,
    port: int | None = _MCP_PORT,
    path: str | None = _MCP_PATH,
    api_key: str | None = _MCP_API_KEY,
    no_api_key: bool = _MCP_NO_API_KEY,
    public_url: str | None = _MCP_PUBLIC_URL,
    allowed_hosts: list[str] | None = _MCP_ALLOWED_HOSTS,
    allowed_origins: list[str] | None = _MCP_ALLOWED_ORIGINS,
    disable_dns_rebinding: bool | None = _MCP_DISABLE_DNS_REBINDING,
) -> None:
    """Run the MCP server over stdio or Streamable HTTP."""
    _serve(
        project,
        transport=transport,
        host=host,
        port=port,
        path=path,
        api_key=api_key,
        no_api_key=no_api_key,
        public_url=public_url,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        disable_dns_rebinding=disable_dns_rebinding,
    )


@mcp_app.command("serve")
def mcp_serve(
    project: Path | None = _ROOT,
    transport: str | None = _MCP_TRANSPORT,
    host: str | None = _MCP_HOST,
    port: int | None = _MCP_PORT,
    path: str | None = _MCP_PATH,
    api_key: str | None = _MCP_API_KEY,
    no_api_key: bool = _MCP_NO_API_KEY,
    public_url: str | None = _MCP_PUBLIC_URL,
    allowed_hosts: list[str] | None = _MCP_ALLOWED_HOSTS,
    allowed_origins: list[str] | None = _MCP_ALLOWED_ORIGINS,
    disable_dns_rebinding: bool | None = _MCP_DISABLE_DNS_REBINDING,
) -> None:
    """Run the MCP server (alias for ``serve``)."""
    _serve(
        project,
        transport=transport,
        host=host,
        port=port,
        path=path,
        api_key=api_key,
        no_api_key=no_api_key,
        public_url=public_url,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        disable_dns_rebinding=disable_dns_rebinding,
    )


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
    pattern: str | None = typer.Argument(None),
    path: str | None = typer.Option(None, "--path"),
    glob: list[str] | None = _GLOB_FILTER,
    file_type: str | None = typer.Option(None, "--type"),
    output_mode: str = typer.Option("content", "--output-mode"),
    case_insensitive: bool = typer.Option(False, "--case-insensitive", "-i"),
    context_lines: int | None = typer.Option(None, "--context", "-C"),
    multiline: bool = typer.Option(False, "--multiline"),
    head_limit: int | None = typer.Option(None, "--head-limit"),
    offset: int | None = typer.Option(None, "--offset"),
    include_all: bool = typer.Option(False, "--include-all"),
    exclude: list[str] | None = _EXCLUDE_FILTER,
    reference_kind: list[str] | None = _REFERENCE_KIND_FILTER,
    exclude_reference_kind: list[str] | None = _EXCLUDE_REFERENCE_KIND_FILTER,
    project: Path | None = _ROOT,
) -> None:
    """Search file contents with a regular expression."""
    glob_arg: str | list[str] | None
    if glob is None:
        glob_arg = None
    elif len(glob) == 1:
        glob_arg = glob[0]
    else:
        glob_arg = glob
    exclude_arg: str | list[str] | None
    if exclude is None:
        exclude_arg = None
    elif len(exclude) == 1:
        exclude_arg = exclude[0]
    else:
        exclude_arg = exclude
    _run(
        project,
        lambda session: tools.grep(
            session.guard,
            pattern=pattern,
            path=path,
            glob=glob_arg,
            file_type=file_type,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context_lines=context_lines,
            multiline=multiline,
            head_limit=head_limit,
            offset=offset,
            include_all=include_all,
            exclude=exclude_arg,
            reference_kind=reference_kind,
            exclude_reference_kind=exclude_reference_kind,
        ),
    )


@app.command(name="glob")
def glob_command(
    glob_pattern: list[str] = _GLOB_PATTERNS,
    target_directory: str | None = typer.Option(None, "--dir"),
    include_all: bool = typer.Option(False, "--include-all"),
    exclude: list[str] | None = _EXCLUDE_FILTER,
    project: Path | None = _ROOT,
) -> None:
    """Find files matching a glob pattern."""
    pattern_arg: str | list[str] = glob_pattern[0] if len(glob_pattern) == 1 else glob_pattern
    exclude_arg: str | list[str] | None
    if exclude is None:
        exclude_arg = None
    elif len(exclude) == 1:
        exclude_arg = exclude[0]
    else:
        exclude_arg = exclude
    _run(
        project,
        lambda session: tools.glob(
            session.guard,
            glob_pattern=pattern_arg,
            target_directory=target_directory,
            include_all=include_all,
            exclude=exclude_arg,
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
def write(
    path: str,
    contents: str,
    description: str | None = typer.Option(None, "--description", "-m"),
    group_id: str | None = typer.Option(None, "--group-id"),
    group_title: str | None = typer.Option(None, "--group-title"),
    project: Path | None = _ROOT,
) -> None:
    """Create or overwrite a file."""
    _run(
        project,
        lambda session: tools.write(
            session.guard,
            session.history,
            path=path,
            contents=contents,
            description=description,
            group_id=group_id,
            group_title=group_title,
        ),
    )


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
    description: str | None = typer.Option(None, "--description", "-m"),
    group_id: str | None = typer.Option(None, "--group-id"),
    group_title: str | None = typer.Option(None, "--group-title"),
    project: Path | None = _ROOT,
) -> None:
    """Replace text, tolerating LF/CRLF differences by default."""
    _run(
        project,
        lambda session: tools.str_replace(
            session.guard,
            session.history,
            path=path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            ignore_line_endings=ignore_line_endings,
            expected_occurrences=expected_occurrences,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
            description=description,
            group_id=group_id,
            group_title=group_title,
        ),
    )


@app.command(name="apply-patch")
def apply_patch_command(
    patch_file: Path,
    description: str | None = typer.Option(None, "--description", "-m"),
    group_id: str | None = typer.Option(None, "--group-id"),
    group_title: str | None = typer.Option(None, "--group-title"),
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
            description=description,
            group_id=group_id,
            group_title=group_title,
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
def delete(
    path: str,
    description: str | None = typer.Option(None, "--description", "-m"),
    group_id: str | None = typer.Option(None, "--group-id"),
    group_title: str | None = typer.Option(None, "--group-title"),
    project: Path | None = _ROOT,
) -> None:
    """Delete a file."""
    _run(
        project,
        lambda session: tools.delete(
            session.guard,
            session.history,
            path=path,
            description=description,
            group_id=group_id,
            group_title=group_title,
        ),
    )


@app.command()
def review(
    identifier: str = typer.Argument("latest"),
    no_open: bool = typer.Option(False, "--no-open"),
    project: Path | None = _ROOT,
) -> None:
    """Open a local patch review and keep it available until interrupted."""
    session = Session.create(project)
    try:
        result = session.reviews.open(identifier, open_browser=not no_open)
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
