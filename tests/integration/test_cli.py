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
    assert any(item["path"] == "src/agenda.py" for item in json.loads(listed.stdout)["data"])
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


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"
