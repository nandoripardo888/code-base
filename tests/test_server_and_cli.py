from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness.cli import app
from code_harness.errors import PathOutsideProjectError
from code_harness.mcp.server import create_server
from code_harness.session import Session, resolve_project_root
from tests.conftest import requires_git, requires_ripgrep

runner = CliRunner()

EXPECTED_TOOLS = {
    "Shell",
    "GetJobStatus",
    "Grep",
    "Glob",
    "Read",
    "Write",
    "StrReplace",
    "ApplyPatch",
    "OpenPatchReview",
    "RollbackPatch",
    "Delete",
}


def test_server_exposes_shell_status_and_file_tools(session: Session) -> None:
    server = create_server(session=session)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == EXPECTED_TOOLS


def test_tool_schemas_match_the_cursor_contract(session: Session) -> None:
    server = create_server(session=session)
    schemas = {tool.name: tool.inputSchema for tool in asyncio.run(server.list_tools())}

    assert schemas["Shell"]["required"] == ["command"]
    assert set(schemas["Shell"]["properties"]) == {
        "command",
        "working_directory",
        "block_until_ms",
        "description",
        "shell",
    }
    assert schemas["GetJobStatus"]["required"] == ["job_id"]
    assert set(schemas["GetJobStatus"]["properties"]) == {"job_id", "wait_ms", "tail_lines"}
    assert schemas["Grep"]["required"] == ["pattern"]
    assert "type" in schemas["Grep"]["properties"]
    assert schemas["Grep"]["properties"]["output_mode"]["enum"] == [
        "content",
        "files_with_matches",
        "count",
        "symbols",
    ]
    assert schemas["Glob"]["required"] == ["glob_pattern"]
    assert schemas["Read"]["required"] == ["path"]
    assert set(schemas["Write"]["required"]) == {"path", "contents"}
    assert set(schemas["StrReplace"]["required"]) == {"path", "old_string", "new_string"}
    assert set(schemas["StrReplace"]["properties"]) == {
        "path",
        "old_string",
        "new_string",
        "replace_all",
        "ignore_line_endings",
        "expected_occurrences",
        "expected_sha256",
        "dry_run",
    }
    assert schemas["ApplyPatch"]["required"] == ["patch"]
    assert set(schemas["ApplyPatch"]["properties"]) == {
        "patch",
        "description",
        "dry_run",
        "expected_hashes",
    }
    assert schemas["OpenPatchReview"]["properties"]["transaction_id"]["default"] == "latest"
    assert schemas["OpenPatchReview"]["properties"]["open_browser"]["default"] is True
    assert schemas["RollbackPatch"]["required"] == ["transaction_id"]
    assert set(schemas["RollbackPatch"]["properties"]) == {"transaction_id", "force"}
    assert schemas["Delete"]["required"] == ["path"]


def test_session_resolves_root_from_environment(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_PROJECT", str(project))
    assert resolve_project_root() == project.resolve()


def test_session_does_not_expose_background_output_to_read(project: Path) -> None:
    session = Session.create(project)
    try:
        target = session.jobs.directory / "probe.txt"
        target.write_text("visible", encoding="utf-8")
        with pytest.raises(PathOutsideProjectError, match="outside the project root"):
            session.guard.resolve(str(target), kind="file")
    finally:
        session.shutdown()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0


def test_cli_mcp_serve_is_registered() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.stdout


def test_cli_read(project: Path) -> None:
    result = runner.invoke(app, ["read", "src/hello.py", "--project", str(project)])
    assert result.exit_code == 0
    assert "1|def hello():" in result.stdout


def test_cli_reports_errors(project: Path) -> None:
    result = runner.invoke(app, ["read", "missing.txt", "--project", str(project)])
    assert result.exit_code == 1


def test_cli_write_and_delete(project: Path) -> None:
    written = runner.invoke(app, ["write", "tmp.txt", "body", "--project", str(project)])
    assert written.exit_code == 0
    assert (project / "tmp.txt").exists()

    removed = runner.invoke(app, ["delete", "tmp.txt", "--project", str(project)])
    assert removed.exit_code == 0
    assert not (project / "tmp.txt").exists()


def test_cli_str_replace(project: Path) -> None:
    result = runner.invoke(
        app,
        ["str-replace", "src/hello.py", "world", "terra", "--project", str(project)],
    )
    assert result.exit_code == 0
    assert "terra" in (project / "src" / "hello.py").read_text(encoding="utf-8")


@requires_git
def test_cli_apply_and_rollback_patch(project: Path, tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(
        """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'world'
+    return 'terra'
""",
        encoding="utf-8",
    )
    applied = runner.invoke(
        app,
        ["apply-patch", str(patch_file), "--project", str(project)],
    )
    assert applied.exit_code == 0
    import json

    transaction_id = json.loads(applied.stdout)["transaction_id"]
    rolled_back = runner.invoke(
        app,
        ["rollback-patch", transaction_id, "--project", str(project)],
    )
    assert rolled_back.exit_code == 0
    assert "world" in (project / "src" / "hello.py").read_text(encoding="utf-8")


def test_cli_shell(project: Path) -> None:
    result = runner.invoke(
        app,
        ["shell", "python -c \"print('cli-shell')\"", "--project", str(project)],
    )
    assert result.exit_code == 0
    assert "cli-shell" in result.stdout


@requires_ripgrep
def test_cli_grep_and_glob(project: Path) -> None:
    grep_result = runner.invoke(app, ["grep", "hello", "--project", str(project)])
    assert grep_result.exit_code == 0
    assert "src/hello.py" in grep_result.stdout

    glob_result = runner.invoke(app, ["glob", "*.py", "--project", str(project)])
    assert glob_result.exit_code == 0
    assert "src/hello.py" in glob_result.stdout
