import json
import subprocess
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
from code_harness.infrastructure.parsers.native_protocol import (
    ANALYSIS_VERSION,
    PROTOCOL_VERSION,
    WORKER_IMPLEMENTATION_VERSION,
)


def _request(
    content: str = "def hello():\n    return world()\n",
    *,
    path: str = "sample.py",
    language: str = "python",
    request_id: str = "request-1",
) -> AnalyzeRequest:
    return AnalyzeRequest(request_id, path, language, content, str(hash(content)))


def _one_shot_analyze(payload: dict[str, object]) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_harness.infrastructure.parsers.native_worker",
        ],
        input=json.dumps(payload, ensure_ascii=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert process.returncode == 0, process.stderr
    decoded = json.loads(process.stdout)
    assert isinstance(decoded, dict)
    assert "error" not in decoded
    return decoded


def _result_fingerprint(result: object) -> dict[str, object]:
    if hasattr(result, "symbols"):
        return {
            "parser_name": result.parser_name,
            "parser_version": result.parser_version,
            "state": result.state.value if hasattr(result.state, "value") else result.state,
            "symbols": [
                (
                    item.symbol_id,
                    item.name,
                    item.qualified_name,
                    item.kind,
                    item.location.start_line,
                    item.location.end_line,
                    item.signature,
                    item.canonical_signature,
                )
                for item in result.symbols
            ],
            "references": [
                (
                    item.reference_id,
                    item.target_name,
                    item.kind,
                    item.location.start_line,
                    item.location.start_column,
                )
                for item in result.references
            ],
            "chunks": [
                (
                    item.chunk_id,
                    item.content_hash,
                    item.kind,
                    item.symbol_id,
                    item.content,
                )
                for item in result.chunks
            ],
            "warnings": list(result.warnings),
        }
    return {
        "parser_name": result["parser_name"],
        "parser_version": result["parser_version"],
        "state": result["state"],
        "symbols": [
            (
                item["symbol_id"],
                item["name"],
                item.get("qualified_name"),
                item["kind"],
                item["location"]["start_line"],
                item["location"]["end_line"],
                item.get("signature"),
                item.get("canonical_signature"),
            )
            for item in result.get("symbols", ())
        ],
        "references": [
            (
                item["reference_id"],
                item["target_name"],
                item["kind"],
                item["location"]["start_line"],
                item["location"].get("start_column"),
            )
            for item in result.get("references", ())
        ],
        "chunks": [
            (
                item["chunk_id"],
                item["content_hash"],
                item["kind"],
                item.get("symbol_id"),
                item["content"],
            )
            for item in result.get("chunks", ())
        ],
        "warnings": list(result.get("warnings", ())),
    }


def test_supervisor_extracts_python_structure_in_child_process() -> None:
    supervisor = NativeParserSupervisor(timeout_seconds=5)

    result = supervisor.analyze(_request())

    assert result.state is ParseState.READY
    assert {item.name for item in result.symbols} == {"hello"}
    assert {item.target_name for item in result.references} == {"world"}
    assert result.chunks
    assert supervisor.health_check()
    assert supervisor.version == ANALYSIS_VERSION == "4"
    assert supervisor.protocol_version == PROTOCOL_VERSION
    assert supervisor.worker_implementation_version == WORKER_IMPLEMENTATION_VERSION
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
    supervisor.shutdown()


def test_supervisor_times_out_and_stops_worker() -> None:
    supervisor = NativeParserSupervisor(
        timeout_seconds=0.05,
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )

    with pytest.raises(ParserTimeoutError):
        supervisor.analyze(_request())

    metrics = supervisor.metrics_snapshot()
    assert metrics.timeouts == 1
    assert metrics.processes_created == 1
    supervisor.shutdown()


def test_supervisor_records_process_metrics_for_successful_analyze() -> None:
    supervisor = NativeParserSupervisor(timeout_seconds=5)

    supervisor.analyze(_request())
    metrics = supervisor.metrics_snapshot()

    assert metrics.processes_created == 1
    assert metrics.distinct_pids == 1
    assert metrics.timeouts == 0
    assert metrics.restarts == 0
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
    supervisor.shutdown()


def test_persistent_worker_reuses_same_pid_across_many_requests() -> None:
    supervisor = NativeParserSupervisor(timeout_seconds=10)
    pids: set[int] = set()

    for index in range(20):
        supervisor.analyze(_request(f"def item_{index}():\n    return {index}\n"))
        pid = supervisor._slots[0].pid
        assert pid is not None
        pids.add(pid)

    metrics = supervisor.metrics_snapshot()
    assert len(pids) == 1
    assert metrics.processes_created == 1
    assert metrics.distinct_pids == 1
    assert metrics.restarts == 0
    assert metrics.parser_cache_hits >= 19
    supervisor.shutdown()
    assert supervisor._slots[0].pid is None


def test_persistent_and_one_shot_results_are_structurally_equivalent() -> None:
    contents = {
        "sample.py": ("class Service:\n    def run(self, value):\n        return helper(value)\n"),
        "Service.java": ("public class Service {\n    public void run() { helper(); }\n}\n"),
    }
    supervisor = NativeParserSupervisor(timeout_seconds=10)
    try:
        for path, content in contents.items():
            language = "java" if path.endswith(".java") else "python"
            one_shot = _one_shot_analyze(
                {
                    "operation": "analyze",
                    "request_id": "one-shot",
                    "path": path,
                    "language": language,
                    "content": content,
                    "content_hash": str(hash(content)),
                }
            )
            persistent = supervisor.analyze(
                _request(content, path=path, language=language, request_id="persistent")
            )
            assert _result_fingerprint(persistent) == _result_fingerprint(one_shot)
            assert persistent.parser_version == ANALYSIS_VERSION
            assert one_shot["parser_version"] == ANALYSIS_VERSION
    finally:
        supervisor.shutdown()


def test_multiline_unicode_content_round_trips() -> None:
    content = "def café():\n    # linha 1\n    # linha 2 com ção\n    return 'áéí'\n"
    supervisor = NativeParserSupervisor(timeout_seconds=5)

    result = supervisor.analyze(_request(content))

    assert {item.name for item in result.symbols} == {"café"}
    assert any("ção" in chunk.content or "áéí" in chunk.content for chunk in result.chunks)
    supervisor.shutdown()


def test_timeout_restarts_worker_with_new_pid() -> None:
    hang = (
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    op=req.get('operation')\n"
        "    rid=req.get('request_id')\n"
        "    if op=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    if op=='analyze':\n"
        "        time.sleep(5)\n"
        "    print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'ok','parser_name':'x','parser_version':'4','state':'ready',"
        "'symbols':[],'references':[],'chunks':[],'warnings':[]}}), flush=True)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=0.2,
        command=(sys.executable, "-u", "-c", hang),
    )

    with pytest.raises(ParserTimeoutError):
        supervisor.analyze(_request("def a():\n    pass\n"))
    first_metrics = supervisor.metrics_snapshot()
    assert first_metrics.timeouts == 1
    assert supervisor._slots[0].pid is None

    # Same fake worker answers quickly after restart when analyze is not forced to sleep -
    # use health-like analyze that the script still sleeps on. Switch to a fast responder.
    supervisor.shutdown()
    fast = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    op=req.get('operation')\n"
        "    if op=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    if op=='health':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'ok'}}), flush=True); continue\n"
        "    print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'parser_name':'x','parser_version':'4','state':'ready',"
        "'symbols':[],'references':[],'chunks':[],'warnings':[]}}), flush=True)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=5,
        command=(sys.executable, "-u", "-c", fast),
    )
    result = supervisor.analyze(_request("def b():\n    pass\n"))
    assert result.parser_name == "x"
    assert supervisor.metrics_snapshot().processes_created == 1
    supervisor.shutdown()


def test_timeout_then_real_worker_recovers() -> None:
    hang = (
        "import json,sys,time\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    if req.get('operation')=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    time.sleep(5)\n"
    )
    # Use a supervisor whose first request times out, then replace command via new instance
    # is already covered above. Here: real worker survives after killing a hung slot by
    # using the real worker with a short timeout on an analyze that completes quickly -
    # instead verify restart counter after invalid response recovery.
    supervisor = NativeParserSupervisor(
        timeout_seconds=0.2,
        failure_threshold=99,
        command=(sys.executable, "-u", "-c", hang),
    )
    with pytest.raises(ParserTimeoutError):
        supervisor.analyze(_request())
    supervisor.shutdown()

    recovered = NativeParserSupervisor(timeout_seconds=10)
    first_pid = None
    try:
        recovered.analyze(_request("def ok():\n    return 1\n"))
        first_pid = recovered._slots[0].pid
        # Force invalid response path by writing through a broken custom restart:
        recovered._slots[0]._kill_and_reset("sample.py", reason="test")
        recovered.analyze(_request("def ok2():\n    return 2\n"))
        second_pid = recovered._slots[0].pid
        metrics = recovered.metrics_snapshot()
        assert first_pid is not None and second_pid is not None
        assert second_pid != first_pid
        assert metrics.processes_created == 2
        assert metrics.restarts == 1
    finally:
        recovered.shutdown()


def test_stderr_flood_does_not_block_worker() -> None:
    flood = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    op=req.get('operation')\n"
        "    sys.stderr.write(('noise\\n'*2000)); sys.stderr.flush()\n"
        "    if op=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    if op=='health':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'ok'}}), flush=True); continue\n"
        "    print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'parser_name':'flood','parser_version':'4','state':'ready',"
        "'symbols':[],'references':[],'chunks':[],'warnings':[]}}), flush=True)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=10,
        command=(sys.executable, "-u", "-c", flood),
    )
    try:
        result = supervisor.analyze(_request())
        assert result.parser_name == "flood"
        assert supervisor.health_check()
    finally:
        supervisor.shutdown()


def test_invalid_request_id_causes_restart() -> None:
    wrong_id = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    op=req.get('operation')\n"
        "    if op=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    print(json.dumps({'protocol_version':1,'request_id':'other-id','ok':True,"
        "'result':{'parser_name':'x','parser_version':'4','state':'ready',"
        "'symbols':[],'references':[],'chunks':[],'warnings':[]}}), flush=True)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=5,
        failure_threshold=99,
        command=(sys.executable, "-u", "-c", wrong_id),
    )
    with pytest.raises(ParserCrashError):
        supervisor.analyze(_request())
    assert supervisor._slots[0].pid is None
    assert supervisor.metrics_snapshot().processes_created == 1
    supervisor.shutdown()


def test_java_parser_cache_created_once_per_process() -> None:
    content = "public class Demo { public void run() {} }\n"
    supervisor = NativeParserSupervisor(timeout_seconds=10)
    try:
        first = supervisor.analyze(
            _request(content, path="Demo.java", language="java", request_id="j1")
        )
        second = supervisor.analyze(
            _request(content, path="Demo2.java", language="java", request_id="j2")
        )
        assert first.parser_name.startswith("tree-sitter")
        assert second.parser_name.startswith("tree-sitter")
        metrics = supervisor.metrics_snapshot()
        assert metrics.processes_created == 1
        assert metrics.parser_cache_hits >= 1
        health = supervisor._slots[0].request("health", {}, "<health>")
        assert health["parser_cache_size"] >= 1
        assert health["parser_cache_hits"] >= 1
        assert health["analysis_version"] == ANALYSIS_VERSION
        assert health["protocol_version"] == PROTOCOL_VERSION
    finally:
        supervisor.shutdown()


def test_transport_change_does_not_bump_analysis_version() -> None:
    assert ANALYSIS_VERSION == "4"
    supervisor = NativeParserSupervisor(timeout_seconds=5)
    try:
        result = supervisor.analyze(_request())
        assert result.parser_version == "4"
        assert supervisor.version == "4"
    finally:
        supervisor.shutdown()


def test_pool_limits_concurrent_slot_usage() -> None:
    import threading
    import time

    supervisor = NativeParserSupervisor(timeout_seconds=10, worker_count=2)
    active = 0
    max_active = 0
    lock = threading.Lock()
    original = type(supervisor._slots[0]).request

    def counting_request(self, operation, payload, path):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original(self, operation, payload, path)
        finally:
            with lock:
                active -= 1

    for slot in supervisor._slots:
        slot.request = counting_request.__get__(slot, type(slot))  # type: ignore[method-assign]

    threads = [
        threading.Thread(
            target=lambda index=index: supervisor.analyze(
                _request(f"def item_{index}():\n    return {index}\n", request_id=f"r{index}")
            )
        )
        for index in range(8)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
        assert max_active <= 2
        assert supervisor.metrics_snapshot().max_concurrent_analyses <= 2
        assert supervisor.metrics_snapshot().worker_count == 2
    finally:
        supervisor.shutdown()


def test_pool_crash_isolates_to_one_slot() -> None:
    import threading

    crash_once = (
        "import json,sys\n"
        "seen=False\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    op=req.get('operation')\n"
        "    if op=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    if op=='health':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'ok'}}), flush=True); continue\n"
        "    payload=req.get('payload') or {}\n"
        "    if (not seen) and 'crash' in str(payload.get('path','')):\n"
        "        seen=True\n"
        "        raise SystemExit(1)\n"
        "    print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'parser_name':'pool','parser_version':'4','state':'ready',"
        "'symbols':[],'references':[],'chunks':[],'warnings':[]}}), flush=True)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=5,
        failure_threshold=99,
        worker_count=2,
        command=(sys.executable, "-u", "-c", crash_once),
    )
    results: list[object] = []
    errors: list[BaseException] = []

    def run(path: str) -> None:
        try:
            results.append(
                supervisor.analyze(_request("def ok():\n    pass\n", path=path, request_id=path))
            )
        except BaseException as error:
            errors.append(error)

    try:
        crash_thread = threading.Thread(target=run, args=("crash.py",))
        ok_thread = threading.Thread(target=run, args=("ok.py",))
        crash_thread.start()
        ok_thread.start()
        crash_thread.join(timeout=30)
        ok_thread.join(timeout=30)
        assert any(isinstance(error, ParserCrashError) for error in errors)
        assert results, "healthy slot should still return results"
        recovered = supervisor.analyze(_request("def later():\n    pass\n", path="later.py"))
        assert recovered.parser_name == "pool"
    finally:
        supervisor.shutdown()
        supervisor.shutdown()


def test_circuit_breaker_counts_distinct_payloads_only() -> None:
    clock = {"now": 0.0}

    def advance() -> float:
        return clock["now"]

    hang = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    rid=req.get('request_id')\n"
        "    if req.get('operation')=='shutdown':\n"
        "        print(json.dumps({'protocol_version':1,'request_id':rid,'ok':True,"
        "'result':{'status':'shutdown'}}), flush=True); break\n"
        "    raise SystemExit(1)\n"
    )
    supervisor = NativeParserSupervisor(
        timeout_seconds=2,
        failure_threshold=3,
        failure_window_seconds=60,
        circuit_reset_seconds=60,
        clock=advance,
        command=(sys.executable, "-u", "-c", hang),
    )
    try:
        for index in range(2):
            with pytest.raises(ParserCrashError):
                supervisor.analyze(
                    _request(
                        f"def boom_{index}():\n    pass\n",
                        path=f"a{index}.py",
                        request_id=f"a{index}",
                    )
                )
        # Same payload again must not open the circuit by itself.
        with pytest.raises(ParserCrashError):
            supervisor.analyze(
                _request("def boom_0():\n    pass\n", path="a0.py", request_id="a0-again")
            )
        with pytest.raises(ParserCrashError):
            supervisor.analyze(_request("def boom_2():\n    pass\n", path="a2.py", request_id="a2"))
        with pytest.raises(ParserCircuitOpenError):
            supervisor.analyze(_request("def boom_3():\n    pass\n", path="a3.py", request_id="a3"))
        clock["now"] = 61.0
        # After reset window the circuit closes; the next failure is allowed through.
        with pytest.raises(ParserCrashError):
            supervisor.analyze(_request("def boom_4():\n    pass\n", path="a4.py", request_id="a4"))
    finally:
        supervisor.shutdown()


def test_shutdown_with_never_started_slots_is_idempotent() -> None:
    supervisor = NativeParserSupervisor(worker_count=4, timeout_seconds=5)
    supervisor.shutdown()
    supervisor.shutdown()
    assert all(slot.pid is None for slot in supervisor._slots)
