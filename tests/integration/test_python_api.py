import shutil
from pathlib import Path

import pytest

from code_harness import CodeHarness
from code_harness.domain.enums import MatchType
from code_harness.domain.errors import CodeHarnessError

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="Ripgrep is unavailable")


def test_python_api_executes_lexical_workflow(fixture_repository: Path) -> None:
    harness = CodeHarness.open(fixture_repository)

    files = harness.list_files()
    path_matches = harness.search_files("AgendaService")
    literal = harness.search_text("montar_agenda_consultor", include_globs=("*.pck", "*.py"))
    regex = harness.search_regex(r"public\s+void\s+montarAgendaConsultor")
    source = harness.read_range("src/AgendaService.java", 3, 6)

    assert "ignored.sql" not in {item.path for item in files.data}
    assert path_matches.data[0].source_file.path == "src/AgendaService.java"
    assert {hit.snippet.location.path for hit in literal.data} == {
        "database/PKG_AGENDA.pck",
        "src/agenda.py",
    }
    assert regex.data[0].match_type is MatchType.REGEX
    assert source.data.location.start_line == 3
    assert len(source.data.file_hash) == 64


def test_python_api_reports_invalid_regex(fixture_repository: Path) -> None:
    with pytest.raises(CodeHarnessError) as captured:
        CodeHarness.open(fixture_repository).search_regex("[")

    assert captured.value.code.value == "invalid_query"
