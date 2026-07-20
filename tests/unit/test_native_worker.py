import pytest

from code_harness.infrastructure.parsers import native_worker


def test_python_extractor_handles_nested_async_imports_and_calls() -> None:
    content = """import os
from sample import helper

class Service:
    async def execute(self):
        return helper()
"""

    symbols, references, warnings = native_worker._analyze_python("service.py", content)

    assert warnings == []
    assert [(item.name, item.kind) for item in symbols] == [
        ("Service", "class"),
        ("execute", "method"),
    ]
    assert {item.target_name for item in references} == {"os", "sample.helper", "helper"}


def test_python_extractor_reports_invalid_syntax() -> None:
    symbols, references, warnings = native_worker._analyze_python("broken.py", "def broken(:\n")

    assert symbols == []
    assert references == []
    assert warnings and "syntax error" in warnings[0]


def test_java_extractor_recognizes_types_methods_imports_and_calls() -> None:
    content = """import java.util.List;
public class Service {
    public Service() { initialize(); }
    public void execute() { helper(); }
}
interface Contract {}
enum State { READY }
record Item(String name) {}
"""

    symbols, references, warnings = native_worker._analyze_java("Service.java", content)

    assert warnings == []
    assert {item.kind for item in symbols} >= {
        "class",
        "interface",
        "enum",
        "record",
        "constructor",
        "method",
    }
    assert {item.target_name for item in references} >= {"java.util.List", "initialize", "helper"}


def test_java_extractor_degrades_when_no_declarations_exist() -> None:
    symbols, references, warnings = native_worker._analyze_java("empty.java", "// empty\n")

    assert symbols == []
    assert references == []
    assert warnings


def test_plsql_extractor_recognizes_package_members_and_calls() -> None:
    content = """create or replace package body pkg_demo as
  procedure execute is
  begin
    helper();
  end execute;
  function value return number is
  begin
    return 1;
  end value;
end pkg_demo;
/
"""

    symbols, references, warnings = native_worker._analyze_plsql("pkg_demo.pck", content)

    assert warnings == []
    assert {item.kind for item in symbols} == {"package_body", "procedure", "function"}
    assert {item.target_name for item in references} == {"helper"}


def test_worker_rejects_unsupported_language() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        native_worker._analyze(
            {
                "path": "sample.txt",
                "language": "text",
                "content": "plain",
                "content_hash": "hash",
            }
        )
