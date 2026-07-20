import sys

import pytest

from code_harness.domain.enums import ParseState
from code_harness.domain.errors import (
    ParserCircuitOpenError,
    ParserCrashError,
    ParserTimeoutError,
)
from code_harness.domain.models.structural import AnalyzeRequest
from code_harness.infrastructure.parsers import NativeParserSupervisor


def _request(content: str = "def hello():\n    return world()\n") -> AnalyzeRequest:
    return AnalyzeRequest("request-1", "sample.py", "python", content, str(hash(content)))


def test_supervisor_extracts_python_structure_in_child_process() -> None:
    supervisor = NativeParserSupervisor(timeout_seconds=5)

    result = supervisor.analyze(_request())

    assert result.state is ParseState.READY
    assert {item.name for item in result.symbols} == {"hello"}
    assert {item.target_name for item in result.references} == {"world"}
    assert result.chunks
    assert supervisor.health_check()
    supervisor.shutdown()
    supervisor.shutdown()


def test_supervisor_preserves_unicode_and_assigns_unique_ids_to_chained_calls() -> None:
    supervisor = NativeParserSupervisor(timeout_seconds=5)
    content = (
        "def ação(valor):\n"
        "    return valor.replace('á', 'a').replace('ção', 'cao').replace('_', '-')\n"
    )

    result = supervisor.analyze(_request(content))
    replace_references = [item for item in result.references if item.target_name == "replace"]

    assert {item.name for item in result.symbols} == {"ação"}
    assert len(replace_references) == 3
    assert len({item.reference_id for item in replace_references}) == 3


def test_supervisor_times_out_and_stops_worker() -> None:
    supervisor = NativeParserSupervisor(
        timeout_seconds=0.05,
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )

    with pytest.raises(ParserTimeoutError):
        supervisor.analyze(_request())

    supervisor.shutdown()


def test_supervisor_opens_circuit_after_invalid_response() -> None:
    supervisor = NativeParserSupervisor(
        failure_threshold=1,
        command=(sys.executable, "-c", "print('not-json')"),
    )

    with pytest.raises(ParserCrashError):
        supervisor.analyze(_request("def first():\n    pass\n"))
    with pytest.raises(ParserCircuitOpenError):
        supervisor.analyze(_request("def changed():\n    pass\n"))
