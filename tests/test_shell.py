from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import code_harness.shell.background as background_module
from code_harness.errors import (
    ExecutionError,
    InvalidArgumentError,
    PathOutsideProjectError,
    ShellSyntaxMismatchError,
    ShellUnavailableError,
)
from code_harness.paths import PathGuard
from code_harness.shell.background import (
    OUTPUT_CURSOR_START,
    TAIL_MAX_BYTES,
    JobPolicy,
    JobRegistry,
    ShellJob,
    tail_output,
)
from code_harness.shell.environment import (
    ShellEnvironment,
    build_shell_argv,
    resolve_environment,
    validate_command_syntax,
)
from code_harness.tools import cancel_job, get_job_status, shell

PYTHON_SHELL = "cmd" if os.name == "nt" else "sh"
SLEEP_COMMAND = f'"{sys.executable}" -c "import time; time.sleep(2)"'


def _python(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def _start_background(guard: PathGuard, jobs: JobRegistry, command: str) -> dict[str, object]:
    result = shell(
        guard,
        jobs,
        command=command,
        block_until_ms=0,
        shell=PYTHON_SHELL,
    )
    assert result["status"] == "running"
    return result


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_shell_returns_structured_output_and_exit_code(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    result = shell(
        guard,
        jobs,
        command=_python("print('hi there')"),
        shell=PYTHON_SHELL,
    )
    assert result["status"] == "completed"
    assert result["job_id"] is None
    assert result["exit_code"] == 0
    assert "hi there" in str(result["output"])
    assert result["environment"]["cwd"] == "."
    assert result["environment"]["shell"] == PYTHON_SHELL


def test_shell_reports_failure_exit_code(guard: PathGuard, jobs: JobRegistry) -> None:
    result = shell(
        guard,
        jobs,
        command=_python("raise SystemExit(3)"),
        shell=PYTHON_SHELL,
    )
    assert result["status"] == "failed"
    assert result["exit_code"] == 3


def test_shell_captures_stderr_and_empty_output(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    stderr = shell(
        guard,
        jobs,
        command=_python("import sys; sys.stderr.write('boom')"),
        shell=PYTHON_SHELL,
    )
    empty = shell(guard, jobs, command=_python("pass"), shell=PYTHON_SHELL)
    assert "boom" in str(stderr["output"])
    assert empty["output"] == ""


def test_shell_runs_in_working_directory(guard: PathGuard, jobs: JobRegistry) -> None:
    result = shell(
        guard,
        jobs,
        command=_python("import os; print(os.path.basename(os.getcwd()))"),
        working_directory="src",
        shell=PYTHON_SHELL,
    )
    assert "src" in str(result["output"])
    assert result["environment"]["cwd"] == "src"


def test_shell_rejects_working_directory_outside_project(
    guard: PathGuard, jobs: JobRegistry, tmp_path: Path
) -> None:
    with pytest.raises(PathOutsideProjectError):
        shell(
            guard,
            jobs,
            command=_python("pass"),
            working_directory=str(tmp_path),
            shell=PYTHON_SHELL,
        )


def test_shell_background_lifecycle(guard: PathGuard, jobs: JobRegistry) -> None:
    result = _start_background(guard, jobs, SLEEP_COMMAND)
    assert str(result["job_id"]).startswith("job-")
    assert result["exit_code"] is None
    assert "output_path" not in result

    running = get_job_status(jobs, job_id=str(result["job_id"]))
    assert running["status"] == "running"
    assert running["exit_code"] is None

    completed = get_job_status(jobs, job_id=str(result["job_id"]), wait_ms=10_000)
    assert completed["status"] == "completed"
    assert completed["exit_code"] == 0


def test_background_failure_and_unknown_job(guard: PathGuard, jobs: JobRegistry) -> None:
    result = _start_background(
        guard,
        jobs,
        _python("import time; time.sleep(.2); raise SystemExit(7)"),
    )
    failed = get_job_status(jobs, job_id=str(result["job_id"]), wait_ms=10_000)
    unknown = get_job_status(jobs, job_id="job-does-not-exist")
    assert failed["status"] == "failed"
    assert failed["exit_code"] == 7
    assert unknown["status"] == "unknown"
    assert unknown["exit_code"] is None


def test_cancel_job_is_idempotent_and_status_remains_cancelled(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    result = _start_background(guard, jobs, SLEEP_COMMAND)
    job_id = str(result["job_id"])

    first = cancel_job(jobs, job_id=job_id)
    status = get_job_status(jobs, job_id=job_id)
    second = cancel_job(jobs, job_id=job_id)

    assert first["status"] == "cancelled"
    assert first["exit_code"] is None
    assert first["already_finished"] is False
    assert status["status"] == "cancelled"
    assert status["exit_code"] is None
    assert second["status"] == "cancelled"
    assert second["already_finished"] is True


def test_cancel_job_preserves_completed_status(guard: PathGuard, jobs: JobRegistry) -> None:
    result = _start_background(
        guard,
        jobs,
        _python("import time; time.sleep(.1); print('done')"),
    )
    job_id = str(result["job_id"])
    completed = get_job_status(jobs, job_id=job_id, wait_ms=10_000)

    cancelled = cancel_job(jobs, job_id=job_id)

    assert completed["status"] == "completed"
    assert cancelled["status"] == "completed"
    assert cancelled["exit_code"] == 0
    assert cancelled["already_finished"] is True


def test_cancel_job_handles_unknown_and_empty_ids(jobs: JobRegistry) -> None:
    unknown = cancel_job(jobs, job_id="job-does-not-exist")
    assert unknown == {
        "job_id": "job-does-not-exist",
        "status": "unknown",
        "exit_code": None,
        "elapsed_ms": 0,
        "already_finished": False,
    }
    with pytest.raises(InvalidArgumentError, match="job_id must not be empty"):
        cancel_job(jobs, job_id="  ")


def test_cancel_job_terminates_child_process_tree(
    guard: PathGuard, jobs: JobRegistry, project: Path
) -> None:
    heartbeat = project / "child-heartbeat.txt"
    child_script = project / "child_worker.py"
    parent_script = project / "parent_worker.py"
    child_script.write_text(
        "import pathlib, sys, time\n"
        "target = pathlib.Path(sys.argv[1])\n"
        "while True:\n"
        "    with target.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('x')\n"
        "    time.sleep(.05)\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child_script)!r}, sys.argv[1]])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    result = _start_background(
        guard,
        jobs,
        f'"{sys.executable}" "{parent_script}" "{heartbeat}"',
    )
    assert _wait_until(lambda: heartbeat.exists() and heartbeat.stat().st_size >= 2)

    cancelled = cancel_job(jobs, job_id=str(result["job_id"]))
    time.sleep(0.2)
    size_after_cancel = heartbeat.stat().st_size
    time.sleep(0.3)

    assert cancelled["status"] == "cancelled"
    assert heartbeat.stat().st_size == size_after_cancel


def test_cancel_job_targets_posix_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 1234

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.return_code = -15
            return self.return_code

        def terminate(self) -> None:
            self.return_code = -15

        def kill(self) -> None:
            self.return_code = -9

    process = FakeProcess()
    monkeypatch.setattr(background_module.os, "name", "posix")
    monkeypatch.setitem(background_module.os.__dict__, "getpgid", lambda _pid: 9876)
    monkeypatch.setitem(
        background_module.os.__dict__,
        "killpg",
        lambda process_group, signal_number: calls.append((process_group, signal_number)),
    )
    job = ShellJob(
        job_id="job-posix",
        process=process,  # type: ignore[arg-type]
        command="sleep",
        working_directory=tmp_path,
        shell_name="sh",
        shell_executable="/bin/sh",
        output_path=tmp_path / "posix.log",
    )

    assert job.cancel() is True
    assert calls == [(9876, background_module.signal.SIGTERM)]
    assert job.status == "cancelled"


def test_concurrent_job_limit_fails_before_creating_process_or_log(
    guard: PathGuard, tmp_path: Path
) -> None:
    registry = JobRegistry(
        tmp_path / "limited-jobs",
        policy=JobPolicy(max_running=1, max_retained=10, retention_seconds=60),
    )
    try:
        first = _start_background(guard, registry, SLEEP_COMMAND)
        logs_before = set(registry.directory.glob("*.log"))

        with pytest.raises(ExecutionError, match="Concurrent shell job limit reached"):
            shell(
                guard,
                registry,
                command=SLEEP_COMMAND,
                block_until_ms=0,
                shell=PYTHON_SHELL,
            )

        assert set(registry.directory.glob("*.log")) == logs_before
        cancel_job(registry, job_id=str(first["job_id"]))
    finally:
        registry.cleanup()


def test_registry_prunes_oldest_completed_job_and_log(
    guard: PathGuard, tmp_path: Path
) -> None:
    registry = JobRegistry(
        tmp_path / "retained-jobs",
        policy=JobPolicy(max_running=2, max_retained=1, retention_seconds=60),
    )
    try:
        first = _start_background(
            guard,
            registry,
            _python("import time; time.sleep(.1)"),
        )
        first_id = str(first["job_id"])
        first_job = registry.get(first_id)
        assert first_job is not None
        first_log = first_job.output_path
        get_job_status(registry, job_id=first_id, wait_ms=10_000)

        second = _start_background(
            guard,
            registry,
            _python("import time; time.sleep(.1)"),
        )
        second_id = str(second["job_id"])
        get_job_status(registry, job_id=second_id, wait_ms=10_000)

        assert registry.get(second_id) is not None
        assert registry.get(first_id) is None
        assert not first_log.exists()
    finally:
        registry.cleanup()


def test_registry_prunes_expired_job_but_never_running_job(
    guard: PathGuard, tmp_path: Path
) -> None:
    registry = JobRegistry(
        tmp_path / "expiring-jobs",
        policy=JobPolicy(max_running=2, max_retained=10, retention_seconds=1),
    )
    try:
        completed = _start_background(
            guard,
            registry,
            _python("import time; time.sleep(.1)"),
        )
        completed_id = str(completed["job_id"])
        get_job_status(registry, job_id=completed_id, wait_ms=10_000)
        completed_job = registry.get(completed_id)
        assert completed_job is not None
        completed_log = completed_job.output_path
        completed_job.finished_at = time.monotonic() - 2

        running = _start_background(guard, registry, SLEEP_COMMAND)
        running_id = str(running["job_id"])

        assert registry.get(completed_id) is None
        assert not completed_log.exists()
        assert registry.get(running_id) is not None
        assert registry.has_running_jobs() is True
        cancel_job(registry, job_id=running_id)
    finally:
        registry.cleanup()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CODE_HARNESS_JOBS_MAX_RUNNING", "0"),
        ("CODE_HARNESS_JOBS_MAX_RETAINED", "-1"),
        ("CODE_HARNESS_JOBS_RETENTION_SECONDS", "invalid"),
    ],
)
def test_registry_rejects_invalid_environment_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    directory = tmp_path / "invalid-policy"
    with pytest.raises(ValueError, match=name):
        JobRegistry(directory)
    assert not directory.exists()


def test_multiple_background_jobs_do_not_interfere(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    first = _start_background(
        guard,
        jobs,
        _python("import time; print('first'); time.sleep(.2)"),
    )
    second = _start_background(
        guard,
        jobs,
        _python("import time; print('second'); time.sleep(.2)"),
    )
    results: dict[str, dict[str, object]] = {}

    def collect(name: str, job_id: str) -> None:
        results[name] = get_job_status(jobs, job_id=job_id, wait_ms=10_000)

    threads = [
        threading.Thread(target=collect, args=("first", str(first["job_id"]))),
        threading.Thread(target=collect, args=("second", str(second["job_id"]))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert "first" in str(results["first"]["last_output"])
    assert "second" in str(results["second"]["last_output"])


def test_background_log_contains_no_internal_metadata(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    result = _start_background(guard, jobs, _python("print('streamed')"))
    status = get_job_status(jobs, job_id=str(result["job_id"]), wait_ms=10_000)
    job = jobs.get(str(result["job_id"]))
    assert job is not None
    raw = job.output_path.read_bytes()
    assert "streamed" in str(status["last_output"])
    assert b"exit_code" not in raw
    assert b"elapsed_ms" not in raw


def test_tail_output_limits_lines_bytes_and_handles_utf8(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes("a\u00e7\u00e3o\r\nsecond\r\nthird\r\n".encode())
    by_lines = tail_output(path, max_lines=2, max_bytes=64 * 1024)
    by_bytes = tail_output(path, max_lines=500, max_bytes=8)
    assert by_lines.output == "second\r\nthird\r\n"
    assert by_lines.truncated
    assert by_bytes.truncated
    assert "third" in by_bytes.output


def test_tail_output_tolerates_file_being_written(tmp_path: Path) -> None:
    path = tmp_path / "growing.log"
    path.write_bytes(b"start\n")
    with path.open("ab", buffering=0) as stream:
        stream.write("continua\u00e7\u00e3o\n".encode())
        result = tail_output(path, max_lines=50)
    assert "continua\u00e7\u00e3o" in result.output


def test_get_job_status_without_cursor_preserves_legacy_shape(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    result = _start_background(
        guard,
        jobs,
        _python("import time; time.sleep(.1); print('legacy')"),
    )

    status = get_job_status(jobs, job_id=str(result["job_id"]), wait_ms=10_000)

    assert set(status) == {
        "job_id",
        "status",
        "pid",
        "exit_code",
        "elapsed_ms",
        "last_output",
        "output_truncated",
    }
    assert status["last_output"] == f"legacy{os.linesep}"


def test_get_job_status_cursor_returns_only_new_output(
    guard: PathGuard, jobs: JobRegistry, project: Path
) -> None:
    script = project / "incremental_output.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdout.write('first\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(.4)\n"
        "sys.stdout.write('second\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    result = _start_background(guard, jobs, f'"{sys.executable}" "{script}"')
    job_id = str(result["job_id"])
    job = jobs.get(job_id)
    assert job is not None
    assert _wait_until(lambda: job.output_path.stat().st_size >= len(b"first\n"))

    first = get_job_status(jobs, job_id=job_id, cursor=OUTPUT_CURSOR_START)
    second = get_job_status(
        jobs,
        job_id=job_id,
        cursor=str(first["next_cursor"]),
        wait_ms=10_000,
    )
    third = get_job_status(
        jobs,
        job_id=job_id,
        cursor=str(second["next_cursor"]),
    )

    assert first["output"] == f"first{os.linesep}"
    assert second["output"] == f"second{os.linesep}"
    assert "first" not in str(second["output"])
    assert second["has_more_output"] is False
    assert third["output"] == ""
    assert third["has_more_output"] is False


def test_get_job_status_cursor_drains_more_than_one_chunk(
    guard: PathGuard, jobs: JobRegistry, project: Path
) -> None:
    output = "x" * (TAIL_MAX_BYTES + 4_321)
    script = project / "large_output.py"
    script.write_text(
        "import sys, time\n"
        "time.sleep(.1)\n"
        f"sys.stdout.write({output!r})\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    result = _start_background(guard, jobs, f'"{sys.executable}" "{script}"')
    job_id = str(result["job_id"])
    completed = get_job_status(jobs, job_id=job_id, wait_ms=10_000)
    assert completed["status"] == "completed"

    cursor = OUTPUT_CURSOR_START
    chunks: list[str] = []
    has_more_values: list[bool] = []
    for _ in range(4):
        status = get_job_status(jobs, job_id=job_id, cursor=cursor)
        chunks.append(str(status["output"]))
        has_more_values.append(bool(status["has_more_output"]))
        cursor = str(status["next_cursor"])
        if not status["has_more_output"]:
            break

    assert "".join(chunks) == output
    assert [len(chunk) for chunk in chunks] == [TAIL_MAX_BYTES, 4_321]
    assert has_more_values == [True, False]


def test_get_job_status_cursor_retains_partial_utf8_until_next_poll(
    guard: PathGuard, jobs: JobRegistry, project: Path
) -> None:
    script = project / "partial_utf8.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdout.buffer.write(b'\\xc3')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(.4)\n"
        "sys.stdout.buffer.write(b'\\xa9\\n')\n"
        "sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    result = _start_background(guard, jobs, f'"{sys.executable}" "{script}"')
    job_id = str(result["job_id"])
    job = jobs.get(job_id)
    assert job is not None
    assert _wait_until(lambda: job.output_path.stat().st_size == 1)

    partial = get_job_status(jobs, job_id=job_id, cursor=OUTPUT_CURSOR_START)
    complete = get_job_status(
        jobs,
        job_id=job_id,
        cursor=str(partial["next_cursor"]),
        wait_ms=10_000,
    )

    assert partial["output"] == ""
    assert partial["has_more_output"] is True
    assert complete["output"] == "\u00e9\n"
    assert "\ufffd" not in str(complete["output"])
    assert complete["has_more_output"] is False


def test_get_job_status_cursor_replaces_invalid_terminal_utf8(
    guard: PathGuard, jobs: JobRegistry, project: Path
) -> None:
    script = project / "invalid_utf8.py"
    script.write_text(
        "import sys, time\n"
        "time.sleep(.1)\n"
        "sys.stdout.buffer.write(b'\\xff')\n"
        "sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    result = _start_background(guard, jobs, f'"{sys.executable}" "{script}"')
    job_id = str(result["job_id"])
    get_job_status(jobs, job_id=job_id, wait_ms=10_000)

    status = get_job_status(jobs, job_id=job_id, cursor=OUTPUT_CURSOR_START)

    assert status["output"] == "\ufffd"
    assert status["has_more_output"] is False


def test_get_job_status_cursor_is_opaque_tamper_evident_and_job_bound(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    first = _start_background(
        guard,
        jobs,
        _python("import time; time.sleep(.1); print('first')"),
    )
    second = _start_background(
        guard,
        jobs,
        _python("import time; time.sleep(.1); print('second')"),
    )
    first_id = str(first["job_id"])
    second_id = str(second["job_id"])
    get_job_status(jobs, job_id=first_id, wait_ms=10_000)
    get_job_status(jobs, job_id=second_id, wait_ms=10_000)
    status = get_job_status(jobs, job_id=first_id, cursor=OUTPUT_CURSOR_START)
    cursor = str(status["next_cursor"])

    assert cursor.startswith("v1.")
    assert first_id not in cursor
    with pytest.raises(InvalidArgumentError, match="invalid or expired"):
        get_job_status(jobs, job_id=first_id, cursor=cursor + "x")
    with pytest.raises(InvalidArgumentError, match="invalid or expired"):
        get_job_status(jobs, job_id=second_id, cursor=cursor)


def test_get_job_status_cursor_expires_when_job_is_pruned(
    guard: PathGuard, tmp_path: Path
) -> None:
    registry = JobRegistry(
        tmp_path / "cursor-expiry-jobs",
        policy=JobPolicy(max_running=1, max_retained=10, retention_seconds=1),
    )
    try:
        result = _start_background(
            guard,
            registry,
            _python("import time; time.sleep(.1); print('done')"),
        )
        job_id = str(result["job_id"])
        get_job_status(registry, job_id=job_id, wait_ms=10_000)
        status = get_job_status(registry, job_id=job_id, cursor=OUTPUT_CURSOR_START)
        job = registry.get(job_id)
        assert job is not None
        job.finished_at = time.monotonic() - 2

        with pytest.raises(InvalidArgumentError, match="invalid or expired"):
            get_job_status(
                registry,
                job_id=job_id,
                cursor=str(status["next_cursor"]),
            )
    finally:
        registry.cleanup()


def test_get_job_status_cursor_validates_mode_and_unknown_job(jobs: JobRegistry) -> None:
    unknown = get_job_status(
        jobs,
        job_id="job-does-not-exist",
        cursor=OUTPUT_CURSOR_START,
    )

    assert unknown == {
        "job_id": "job-does-not-exist",
        "status": "unknown",
        "pid": None,
        "exit_code": None,
        "elapsed_ms": 0,
        "output": "",
        "next_cursor": None,
        "has_more_output": False,
    }
    with pytest.raises(InvalidArgumentError, match="tail_lines cannot be combined"):
        get_job_status(
            jobs,
            job_id="job-does-not-exist",
            cursor=OUTPUT_CURSOR_START,
            tail_lines=10,
        )


@pytest.mark.parametrize(
    ("wait_ms", "tail_lines"),
    [(-1, 50), (30_001, 50), (0, 0), (0, 501)],
)
def test_get_job_status_validates_limits(
    jobs: JobRegistry, wait_ms: int, tail_lines: int
) -> None:
    with pytest.raises(InvalidArgumentError):
        get_job_status(
            jobs,
            job_id="job-x",
            wait_ms=wait_ms,
            tail_lines=tail_lines,
        )


@pytest.mark.parametrize("block_until_ms", [-1, 30_001])
def test_shell_validates_block_limit(
    guard: PathGuard, jobs: JobRegistry, block_until_ms: int
) -> None:
    with pytest.raises(InvalidArgumentError):
        shell(
            guard,
            jobs,
            command=_python("pass"),
            block_until_ms=block_until_ms,
            shell=PYTHON_SHELL,
        )


def test_shell_rejects_empty_command(guard: PathGuard, jobs: JobRegistry) -> None:
    with pytest.raises(InvalidArgumentError):
        shell(guard, jobs, command="   ")


def test_explicit_unavailable_shell(
    guard: PathGuard, jobs: JobRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("code_harness.shell.environment._find_explicit", lambda _name: None)
    with pytest.raises(ShellUnavailableError):
        shell(guard, jobs, command="echo hi", shell="bash")


def test_build_shell_argv_for_all_supported_shells() -> None:
    for shell_name in ("powershell", "cmd", "bash", "sh"):
        environment = ShellEnvironment(
            os_name="test",
            shell_name=shell_name,  # type: ignore[arg-type]
            shell_executable=f"/{shell_name}",
            shell_version=None,
            working_directory=".",
        )
        argv = build_shell_argv("echo hi", environment)
        if shell_name == "cmd":
            assert isinstance(argv, str)
            assert argv.startswith("/cmd")
            assert argv.endswith('"echo hi"')
        elif shell_name == "powershell":
            assert isinstance(argv, list)
            assert argv[0] == "/powershell"
            assert argv[-2] == "-Command"
            assert "echo hi" in argv[-1]
            assert "$LASTEXITCODE" in argv[-1]
        else:
            assert isinstance(argv, list)
            assert argv[0] == f"/{shell_name}"
            assert argv[-1] == "echo hi"


def test_auto_shell_honours_override(monkeypatch: pytest.MonkeyPatch) -> None:
    executable = sys.executable
    monkeypatch.setenv("CODE_HARNESS_SHELL", executable)
    environment = resolve_environment("auto", working_directory=".")
    assert environment.shell_executable == executable


def test_auto_shell_prefers_platform_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HARNESS_SHELL", raising=False)
    calls: list[str] = []

    def fake_find(candidate: str) -> str | None:
        calls.append(candidate)
        if os.name == "nt":
            return f"C:/{candidate}.exe" if candidate == "pwsh" else None
        if candidate in {"bash", "/bin/bash"} or candidate.endswith("/bash"):
            return "/bin/bash"
        return None

    monkeypatch.setattr("code_harness.shell.environment._find_executable", fake_find)
    if os.name != "nt":
        monkeypatch.setenv("SHELL", "/bin/bash")
    environment = resolve_environment("auto", working_directory=".")
    if os.name == "nt":
        assert environment.shell_name == "powershell"
        assert calls[0] == "pwsh"
    else:
        assert environment.shell_name == "bash"


def test_windows_auto_falls_back_to_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only fallback order")
    monkeypatch.delenv("CODE_HARNESS_SHELL", raising=False)

    def fake_find(candidate: str) -> str | None:
        if candidate == "powershell":
            return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        return None

    monkeypatch.setattr("code_harness.shell.environment._find_executable", fake_find)
    environment = resolve_environment("auto", working_directory=".")
    assert environment.shell_name == "powershell"
    assert environment.shell_executable.endswith("powershell.exe")


def test_bash_heredoc_is_rejected_in_powershell() -> None:
    with pytest.raises(ShellSyntaxMismatchError, match="Bash heredoc"):
        validate_command_syntax("python <<'PY'\nprint(1)\nPY", "powershell")


def test_powershell_block_is_rejected_in_bash() -> None:
    with pytest.raises(ShellSyntaxMismatchError, match="PowerShell syntax"):
        validate_command_syntax("Get-Content file.txt | Write-Host", "bash")


def test_ambiguous_redirection_produces_warning() -> None:
    warnings = validate_command_syntax("value <<= 1", "powershell")
    assert warnings
    assert "<<" in warnings[0]


def test_registry_cleanup_is_idempotent_and_terminates_job(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    result = _start_background(guard, jobs, SLEEP_COMMAND)
    job = jobs.get(str(result["job_id"]))
    assert job is not None
    output_path = job.output_path
    jobs.cleanup()
    jobs.cleanup()
    assert job.process.poll() is not None
    assert not jobs.directory.exists()
    assert not output_path.exists()


def test_registry_cleanup_without_jobs(tmp_path: Path) -> None:
    registry = JobRegistry(tmp_path / "empty-jobs")
    directory = registry.directory
    registry.cleanup()
    registry.cleanup()
    assert not directory.exists()


def test_shell_recreates_missing_scratch_directory(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    shutil.rmtree(jobs.directory)
    assert not jobs.directory.exists()

    result = shell(
        guard,
        jobs,
        command=_python("print('recovered')"),
        shell=PYTHON_SHELL,
    )
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert "recovered" in str(result["output"])
    assert jobs.directory.exists()


def test_shell_rejects_launch_after_registry_cleanup(
    guard: PathGuard, jobs: JobRegistry
) -> None:
    jobs.cleanup()
    with pytest.raises(ExecutionError, match="already closed"):
        shell(guard, jobs, command=_python("pass"), shell=PYTHON_SHELL)


def test_elapsed_stops_after_completion(guard: PathGuard, jobs: JobRegistry) -> None:
    result = _start_background(guard, jobs, _python("pass"))
    first = get_job_status(jobs, job_id=str(result["job_id"]), wait_ms=10_000)
    time.sleep(0.05)
    second = get_job_status(jobs, job_id=str(result["job_id"]))
    assert second["elapsed_ms"] == first["elapsed_ms"]
