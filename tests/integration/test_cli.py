import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness.interfaces.cli.main import app

runner = CliRunner()
pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="Ripgrep is unavailable")


def test_cli_init_list_search_and_read(copied_repository: Path, tmp_path: Path) -> None:
    environment = {"CODE_HARNESS_HOME": str(tmp_path / "state")}

    initialized = runner.invoke(
        app,
        ["--output", "json", "init", str(copied_repository)],
        env=environment,
    )
    listed = runner.invoke(app, ["--output", "json", "files", "list"], env=environment)
    searched = runner.invoke(
        app,
        ["--output", "json", "search", "text", "AgendaService"],
        env=environment,
    )
    read = runner.invoke(
        app,
        ["read", "src/AgendaService.java", "--lines", "3:5"],
        env=environment,
    )

    assert initialized.exit_code == 0, initialized.output
    assert json.loads(initialized.stdout)["active"] is True
    assert listed.exit_code == 0, listed.output
    assert any(
        item["path"] == "src/agenda.py" for item in json.loads(listed.stdout)["data"]["items"]
    )
    assert searched.exit_code == 0, searched.output
    assert json.loads(searched.stdout)["data"]
    assert read.exit_code == 0, read.output
    assert "public class AgendaService" in read.stdout


def test_cli_returns_stable_error_envelope(fixture_repository: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--project",
            str(fixture_repository),
            "--output",
            "json",
            "read",
            "../outside.py",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stderr)["error"]["code"] == "path_outside_project"


def test_cli_mcp_serve_help_is_available() -> None:
    result = runner.invoke(app, ["mcp", "serve", "--help"])

    assert result.exit_code == 0
    assert "MCP" in result.stdout or "stdio" in result.stdout.lower() or "Serve" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_cli_indexes_reports_status_and_runs_doctor(
    copied_repository: Path, tmp_path: Path
) -> None:
    environment = {"CODE_HARNESS_HOME": str(tmp_path / "state")}
    initialized = runner.invoke(app, ["init", str(copied_repository)], env=environment)
    indexed = runner.invoke(app, ["--output", "json", "index"], env=environment)
    repeated = runner.invoke(app, ["--output", "json", "index"], env=environment)
    status = runner.invoke(app, ["--output", "json", "status"], env=environment)
    doctor = runner.invoke(app, ["--output", "json", "doctor"], env=environment)

    assert initialized.exit_code == 0, initialized.output
    assert indexed.exit_code == 0, indexed.output
    assert "Indexing: discovering files..." in indexed.stderr
    assert "Indexing: writing index to disk..." in indexed.stderr
    assert "Indexing: complete." in indexed.stderr
    assert json.loads(indexed.stdout)["data"]["indexed_files"] > 0
    assert json.loads(repeated.stdout)["data"]["indexed_files"] == 0
    assert json.loads(status.stdout)["data"]["state"] == "ready"
    assert json.loads(doctor.stdout)["data"]["healthy"] is True


def test_cli_model_prepare_requires_semantic_configuration(copied_repository: Path) -> None:
    result = runner.invoke(
        app,
        ["--project", str(copied_repository), "models", "prepare"],
        env={"CODE_HARNESS_SEMANTIC": "0"},
    )

    assert result.exit_code == 4
    assert "embedding_unavailable" in result.stderr


def test_cli_execution_inspect_requires_enablement(copied_repository: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-process",
            "git",
            "status",
            "--short",
        ],
        env={"CODE_HARNESS_EXECUTION": "0"},
    )

    assert result.exit_code == 4
    assert json.loads(result.stderr)["error"]["code"] == "execution_disabled"


def test_cli_execution_inspect_process_when_enabled(
    copied_repository: Path, tmp_path: Path
) -> None:
    environment = {
        "CODE_HARNESS_HOME": str(tmp_path / "state"),
        "CODE_HARNESS_EXECUTION": "1",
    }
    result = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-process",
            "git",
            "status",
            "--short",
        ],
        env=environment,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["decision"] == "allow"
    assert payload["data"]["kind"] == "process"


def test_cli_execution_inspect_powershell_when_enabled(
    copied_repository: Path, tmp_path: Path
) -> None:
    environment = {
        "CODE_HARNESS_HOME": str(tmp_path / "state"),
        "CODE_HARNESS_EXECUTION": "1",
    }
    script_file = copied_repository / "inspect.ps1"
    script_file.write_text("Get-ChildItem\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-powershell",
            "--script",
            "Get-ChildItem",
        ],
        env=environment,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["decision"] == "approval_required"
    assert payload["data"]["kind"] == "powershell"

    from_file = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-powershell",
            "--file",
            "inspect.ps1",
        ],
        env=environment,
    )
    assert from_file.exit_code == 0, from_file.output
    assert json.loads(from_file.stdout)["data"]["script_hash"]

    both = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-powershell",
            "--script",
            "Get-ChildItem",
            "--file",
            "inspect.ps1",
        ],
        env=environment,
    )
    assert both.exit_code == 2
    assert json.loads(both.stderr)["error"]["code"] == "invalid_query"

    neither = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-powershell",
        ],
        env=environment,
    )
    assert neither.exit_code == 2
    assert json.loads(neither.stderr)["error"]["code"] == "invalid_query"

    with_capability = runner.invoke(
        app,
        [
            "--project",
            str(copied_repository),
            "--output",
            "json",
            "execution",
            "inspect-process",
            "git",
            "status",
            "--capability",
            "git_read",
        ],
        env=environment,
    )
    assert with_capability.exit_code == 0, with_capability.output
    assert "git_read" in json.loads(with_capability.stdout)["data"]["requested_capabilities"]
