import shutil
from pathlib import Path

import pytest

from code_harness import CodeHarness
from code_harness.domain.enums import MatchType
from code_harness.domain.errors import CodeHarnessError, ExecutionApprovalRequiredError

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="Ripgrep is unavailable")


def test_python_api_executes_lexical_workflow(fixture_repository: Path) -> None:
    harness = CodeHarness.open(fixture_repository)

    files = harness.list_files()
    path_matches = harness.search_files("AgendaService")
    literal = harness.search_text("montar_agenda_consultor", include_globs=("*.pck", "*.py"))
    regex = harness.search_regex(r"public\s+void\s+montarAgendaConsultor")
    source = harness.read_range("src/AgendaService.java", 3, 6)

    assert "ignored.sql" not in {item.path for item in files.data.items}
    assert path_matches.data[0].source_file.path == "src/AgendaService.java"
    assert {hit.snippet.location.path for hit in literal.data} == {
        "database/PKG_AGENDA.pck",
        "src/agenda.py",
    }
    assert regex.data[0].match_type is MatchType.REGEX
    assert source.data.snippet.location.start_line == 3
    assert len(source.data.snippet.file_hash) == 64


def test_python_api_reports_invalid_regex(fixture_repository: Path) -> None:
    with pytest.raises(CodeHarnessError) as captured:
        CodeHarness.open(fixture_repository).search_regex("[")

    assert captured.value.code.value == "invalid_query"


def test_python_api_administers_local_execution_approval(
    fixture_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "execution-state"))
    harness = CodeHarness.open(fixture_repository)

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        harness.run_process("python", ("-c", "print('review')"))

    approval_id = str(required.value.details["approval_id"])
    assert harness.get_execution_approval(approval_id).data.state.value == "pending"
    assert harness.approve_execution(approval_id, reason="reviewed").data.state.value == "approved"
    assert harness.list_execution_approvals(state="approved").data[0].approval_id == approval_id
