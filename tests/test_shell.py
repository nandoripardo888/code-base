from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from code_harness.errors import (
    ExecutionError,
    InvalidArgumentError,
    PathOutsideProjectError,
    ShellSyntaxMismatchError,
    ShellUnavailableError,
)
from code_harness.paths import PathGuard
from code_harness.shell.background import JobRegistry, tail_output
from code_harness.shell.environment import (
    ShellEnvironment,
    build_shell_argv,
    resolve_environment,
    validate_command_syntax,
)
from code_harness.tools import get_job_status, shell

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
