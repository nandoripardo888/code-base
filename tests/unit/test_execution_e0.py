import os
from pathlib import Path

import pytest

from code_harness.application.dto.execution_requests import (
    InspectPowerShellRequest,
    InspectProcessRequest,
)
from code_harness.application.execution import (
    DeterministicPolicyEngine,
    compute_approval_digest,
    script_hash,
)
from code_harness.bootstrap.container import ApplicationContainer, build_container
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import (
    CommandKind,
    ErrorCode,
    ExecutionCapability,
    PolicyDecision,
)
from code_harness.domain.errors import (
    ExecutionDisabledError,
    ExecutionElevatedSessionError,
    InvalidExecutionRequestError,
    PowerShellParseError,
    PowerShellUnavailableError,
)
from code_harness.domain.models.execution import (
    ExecutionRuntimeConfig,
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    PowerShellAstAnalysis,
)
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.python_api import CodeHarness
from code_harness.interfaces.serialization import serialize_tool_result


def _config(tmp_path: Path, **overrides: object) -> ExecutionRuntimeConfig:
    payload = {
        "backend": "host_supervised",
        "require_approval": True,
        "default_timeout_seconds": 60.0,
        "max_timeout_seconds": 1800.0,
        "max_output_bytes": 200_000,
        "max_processes": 32,
        "allow_elevated": False,
        "execution_home": str(tmp_path / "executions" / "proj"),
        "project_id": "proj",
        "elevated_session": False,
    }
    payload.update(overrides)
    return ExecutionRuntimeConfig(**payload)  # type: ignore[arg-type]


def _analysis(script: str) -> PowerShellAstAnalysis:
    lowered = script.casefold()
    features = []
    for needle, feature in (
        ("invoke-expression", "invoke_expression"),
        ("-encodedcommand", "encoded_command"),
        ("invoke-webrequest", "download_cradle"),
        ("hklm:", "registry"),
        ("start-service", "service_control"),
        ("remove-item", "remove_item"),
    ):
        if needle in lowered:
            features.append(feature)
    return PowerShellAstAnalysis((), (script,), tuple(features))


def test_execution_disabled_by_default(tmp_path: Path) -> None:
    settings = Settings.for_root(tmp_path)
    assert settings.execution_enabled is False
    assert settings.mcp_expose_execution is False
    assert settings.execution_backend == "host_supervised"
    assert settings.execution_require_approval is True
    assert settings.execution_approval_ttl_seconds == 600
    assert settings.execution_allow_elevated is False
    assert (
        settings.execution_home.name == "executions"
        or "executions" in settings.execution_home.parts
    )
    assert tmp_path.resolve() not in settings.execution_home.resolve().parents
    assert settings.execution_home.resolve() != tmp_path.resolve() / ".code-harness"


def test_settings_reads_execution_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "state" / "executions"
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_BACKEND", "host_supervised")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_REQUIRE_APPROVAL", "0")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_DEFAULT_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_MAX_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_MAX_OUTPUT_BYTES", "1000")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_APPROVAL_TTL_SECONDS", "120")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_ALLOW_ELEVATED", "true")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(home))

    settings = Settings.for_root(tmp_path)

    assert settings.execution_enabled is True
    assert settings.execution_require_approval is False
    assert settings.execution_default_timeout_seconds == 30.0
    assert settings.execution_max_timeout_seconds == 90.0
    assert settings.execution_max_output_bytes == 1000
    assert settings.execution_approval_ttl_seconds == 120
    assert settings.execution_allow_elevated is True
    assert settings.execution_home == home.resolve()
    assert settings.execution_project_home() == home.resolve() / settings.project.project_id


def test_mcp_expose_execution_requires_enabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mcp_expose_execution"):
        Settings(
            root=tmp_path,
            index_path=tmp_path / "index.db",
            execution_enabled=False,
            mcp_expose_execution=True,
        )


def test_execution_container_is_none_when_disabled(tmp_path: Path) -> None:
    container = build_container(Settings.for_root(tmp_path))
    assert isinstance(container, ApplicationContainer)
    assert container.execution is None


def test_execution_container_lazy_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    container = build_container(Settings.for_root(tmp_path))
    assert container.execution is not None
    result = container.execution.inspect_process.execute(
        InspectProcessRequest("git", ("status", "--short"))
    )
    assert isinstance(result, ToolResult)
    assert result.data.decision is PolicyDecision.ALLOW


def test_dto_rejects_shell_line_and_duplicates() -> None:
    with pytest.raises(ValueError, match="single program"):
        InspectProcessRequest("git status")
    with pytest.raises(ValueError, match="duplicates"):
        InspectProcessRequest(
            "git",
            ("status",),
            requested_capabilities=(
                ExecutionCapability.GIT_READ,
                ExecutionCapability.GIT_READ,
            ),
        )
    with pytest.raises(ValueError, match="script must not be empty"):
        InspectPowerShellRequest("   ")


def test_policy_autoallow_and_deny(tmp_path: Path) -> None:
    engine = DeterministicPolicyEngine(_config(tmp_path))
    allow = engine.inspect_process(
        NormalizedProcessCommand(
            "git",
            ("status", "--short"),
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        )
    )
    deny = engine.inspect_process(
        NormalizedProcessCommand(
            "git",
            ("push", "origin", "main"),
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        )
    )
    shell = engine.inspect_process(
        NormalizedProcessCommand(
            "cmd.exe",
            ("/c", "dir"),
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        )
    )
    assert allow.decision is PolicyDecision.ALLOW
    assert allow.approval_digest is not None
    assert deny.decision is PolicyDecision.DENY
    assert ExecutionCapability.NETWORK_OUTBOUND in deny.required_capabilities
    assert shell.decision is PolicyDecision.DENY


def test_policy_powershell_surface(tmp_path: Path) -> None:
    engine = DeterministicPolicyEngine(_config(tmp_path))
    simple = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Get-ChildItem",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Get-ChildItem"),
    )
    dynamic = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Invoke-Expression $cmd",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Invoke-Expression $cmd"),
    )
    assert simple.decision is PolicyDecision.APPROVAL_REQUIRED
    assert simple.approval_required is True
    assert dynamic.decision is PolicyDecision.DENY
    assert "invoke_expression" in dynamic.dynamic_features


def test_approval_digest_is_stable(tmp_path: Path) -> None:
    first = compute_approval_digest(
        project_id="proj",
        kind=CommandKind.PROCESS,
        executable="git",
        args=("status",),
        script=None,
        cwd=str(tmp_path),
        timeout_seconds=60.0,
        max_output_bytes=200_000,
        capabilities=(ExecutionCapability.GIT_READ,),
        backend="host_supervised",
        policy_version="1",
        policy_name="deterministic_v1",
    )
    second = compute_approval_digest(
        project_id="proj",
        kind=CommandKind.PROCESS,
        executable="git",
        args=("status",),
        script=None,
        cwd=str(tmp_path),
        timeout_seconds=60.0,
        max_output_bytes=200_000,
        capabilities=(ExecutionCapability.GIT_READ,),
        backend="host_supervised",
        policy_version="1",
        policy_name="deterministic_v1",
    )
    assert first.value == second.value
    assert first.algorithm == "sha256"
    assert script_hash("Write-Output 1") == script_hash("Write-Output 1")


def test_python_api_requires_enablement_and_returns_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = CodeHarness.open(tmp_path)
    with pytest.raises(ExecutionDisabledError) as disabled:
        harness.inspect_process("git", ("status",))
    assert disabled.value.code is ErrorCode.EXECUTION_DISABLED

    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    enabled = CodeHarness.open(tmp_path)
    result = enabled.inspect_process("git", ("status", "--short"))
    assert isinstance(result, ToolResult)
    payload = serialize_tool_result(result)
    assert payload["data"]["decision"] == "allow"
    assert payload["data"]["approval_digest"]["value"]


def test_inspect_rejects_timeout_overflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_DEFAULT_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_MAX_TIMEOUT_SECONDS", "10")
    container = build_container(Settings.for_root(tmp_path))
    assert container.execution is not None
    with pytest.raises(InvalidExecutionRequestError):
        container.execution.inspect_process.execute(
            InspectProcessRequest("git", ("status",), timeout_seconds=11)
        )


def test_elevated_session_blocks_inspection(tmp_path: Path) -> None:
    from code_harness.application.execution import InspectProcessTool
    from code_harness.infrastructure.filesystem import PathGuard

    config = _config(tmp_path, elevated_session=True, allow_elevated=False)
    tool = InspectProcessTool(
        paths=PathGuard(tmp_path),
        policy=DeterministicPolicyEngine(config),
        config=config,
    )
    with pytest.raises(ExecutionElevatedSessionError):
        tool.execute(InspectProcessRequest("git", ("status",)))


def test_policy_covers_common_process_families(tmp_path: Path) -> None:
    engine = DeterministicPolicyEngine(_config(tmp_path))
    cases = [
        ("pytest", ("tests",), PolicyDecision.APPROVAL_REQUIRED),
        ("mvn", ("test",), PolicyDecision.APPROVAL_REQUIRED),
        ("npm", ("install",), PolicyDecision.APPROVAL_REQUIRED),
        ("git", ("reset", "--hard", "HEAD"), PolicyDecision.DENY),
        ("git", ("clean", "-fdx"), PolicyDecision.DENY),
        ("git", ("add", "."), PolicyDecision.APPROVAL_REQUIRED),
        ("unknown-bin", (), PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR),
        ("build.bat", (), PolicyDecision.DENY),
    ]
    for executable, args, expected in cases:
        result = engine.inspect_process(
            NormalizedProcessCommand(
                executable,
                args,
                str(tmp_path),
                60.0,
                200_000,
                (),
                None,
            )
        )
        assert result.decision is expected, executable


def test_policy_powershell_network_and_registry(tmp_path: Path) -> None:
    engine = DeterministicPolicyEngine(_config(tmp_path))
    web = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Invoke-WebRequest https://example.com",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Invoke-WebRequest https://example.com"),
    )
    registry = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Set-ItemProperty HKLM:\\Software\\X -Name Y -Value 1",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Set-ItemProperty HKLM:\\Software\\X -Name Y -Value 1"),
    )
    assert web.decision is PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
    assert ExecutionCapability.NETWORK_OUTBOUND in web.required_capabilities
    assert registry.decision is PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR


def test_dto_and_path_guard_edge_cases(tmp_path: Path) -> None:
    from code_harness.domain.errors import InvalidPathKindError, SourceFileNotFoundError
    from code_harness.infrastructure.filesystem import PathGuard

    with pytest.raises(ValueError):
        InspectProcessRequest("git", timeout_seconds=0)
    with pytest.raises(ValueError):
        InspectPowerShellRequest("Get-ChildItem", max_output_bytes=0)
    with pytest.raises(ValueError):
        InspectProcessRequest("", ("a",))
    with pytest.raises(ValueError):
        InspectPowerShellRequest("x" * 100_001)
    with pytest.raises(ValueError, match="cwd must not be empty"):
        InspectProcessRequest("git", cwd="  ")
    with pytest.raises(ValueError, match="cwd must not be empty"):
        InspectPowerShellRequest("Get-ChildItem", cwd="")
    with pytest.raises(ValueError, match="timeout_seconds"):
        InspectPowerShellRequest("Get-ChildItem", timeout_seconds=0)
    with pytest.raises(ValueError, match="max_output_bytes"):
        InspectProcessRequest("git", max_output_bytes=0)
    with pytest.raises(ValueError, match="args must not exceed"):
        InspectProcessRequest("git", tuple(f"a{i}" for i in range(257)))
    with pytest.raises(ValueError, match="argument length"):
        InspectProcessRequest("git", ("x" * 16_385,))
    with pytest.raises(ValueError, match="reason"):
        InspectProcessRequest("git", reason="r" * 4_001)
    with pytest.raises(ValueError, match="reason"):
        InspectPowerShellRequest("Get-ChildItem", reason="r" * 4_001)
    # Capability values may arrive as strings and are coerced.
    coerced = InspectProcessRequest(
        "git",
        ("status",),
        requested_capabilities=("git_read",),  # type: ignore[arg-type]
    )
    assert coerced.requested_capabilities == (ExecutionCapability.GIT_READ,)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "file.txt").write_text("x", encoding="utf-8")
    nested = root / "dir"
    nested.mkdir()
    guard = PathGuard(root)
    with pytest.raises(InvalidPathKindError):
        guard.resolve_within_root("file.txt", expected_kind="directory", must_exist=True)
    with pytest.raises(InvalidPathKindError):
        guard.resolve_within_root("dir", expected_kind="file", must_exist=True)
    with pytest.raises(SourceFileNotFoundError):
        guard.resolve_within_root("missing-dir", expected_kind="directory", must_exist=True)
    with pytest.raises(ValueError):
        guard.resolve_within_root(".", expected_kind="bogus")  # type: ignore[arg-type]
    absolute, relative = guard.resolve_within_root(".", expected_kind="directory")
    assert relative == "."
    assert Path(absolute) == root.resolve()
    future_abs, future_rel = guard.resolve_within_root(
        "future-file.txt",
        expected_kind="file",
        must_exist=False,
    )
    assert future_rel == "future-file.txt"
    assert Path(future_abs).name == "future-file.txt"
    # Existing file with must_exist=False and expected file is ok.
    file_abs, file_rel = guard.resolve_within_root(
        "file.txt",
        expected_kind="file",
        must_exist=False,
    )
    assert file_rel == "file.txt"
    assert Path(file_abs).is_file()


def test_policy_elevated_and_protected_paths(tmp_path: Path) -> None:
    elevated = DeterministicPolicyEngine(
        _config(tmp_path, elevated_session=True, allow_elevated=False)
    )
    process = elevated.inspect_process(
        NormalizedProcessCommand(
            "git",
            ("status",),
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        )
    )
    powershell = elevated.inspect_powershell(
        NormalizedPowerShellCommand(
            "Get-ChildItem",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Get-ChildItem"),
    )
    assert process.decision is PolicyDecision.DENY
    assert powershell.decision is PolicyDecision.DENY
    assert any(block.code == "elevated_session" for block in process.blocks)

    engine = DeterministicPolicyEngine(_config(tmp_path))
    protected = engine.inspect_process(
        NormalizedProcessCommand(
            "git",
            ("status", ".env"),
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        )
    )
    assert protected.decision is PolicyDecision.DENY
    assert protected.protected_path_matches
    ps_protected = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Get-Content .env",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Get-Content .env"),
    )
    assert ps_protected.decision is PolicyDecision.DENY


def test_policy_process_and_powershell_branches(tmp_path: Path) -> None:
    engine = DeterministicPolicyEngine(_config(tmp_path))
    for executable, args, expected in (
        ("git", ("blame", "HEAD"), PolicyDecision.APPROVAL_REQUIRED),
        ("rg", ("pattern",), PolicyDecision.APPROVAL_REQUIRED),
        ("python", ("script.py",), PolicyDecision.APPROVAL_REQUIRED),
        ("python3", ("--version",), PolicyDecision.ALLOW),
        ("npx", ("install",), PolicyDecision.APPROVAL_REQUIRED),
    ):
        result = engine.inspect_process(
            NormalizedProcessCommand(
                executable,
                args,
                str(tmp_path),
                60.0,
                200_000,
                (),
                None,
            )
        )
        assert result.decision is expected, executable

    service = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Start-Service Spooler",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Start-Service Spooler"),
    )
    remove = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "Remove-Item foo.txt",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("Remove-Item foo.txt"),
    )
    encoded = engine.inspect_powershell(
        NormalizedPowerShellCommand(
            "powershell -EncodedCommand AAA=",
            str(tmp_path),
            60.0,
            200_000,
            (),
            None,
        ),
        _analysis("powershell -EncodedCommand AAA="),
    )
    assert service.decision is PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
    assert ExecutionCapability.SERVICE_CONTROL in service.required_capabilities
    assert remove.decision is PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
    assert encoded.decision is PolicyDecision.DENY


def test_inspect_powershell_tool_timeout_and_elevated(tmp_path: Path) -> None:
    from code_harness.application.execution import InspectPowerShellTool
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer
    from code_harness.infrastructure.filesystem import PathGuard

    config = _config(tmp_path, default_timeout_seconds=5.0, max_timeout_seconds=10.0)
    tool = InspectPowerShellTool(
        paths=PathGuard(tmp_path),
        policy=DeterministicPolicyEngine(config),
        analyzer=PowerShellAstAnalyzer(),
        config=config,
    )
    try:
        ok = tool.execute(InspectPowerShellRequest("Get-ChildItem"))
    except PowerShellUnavailableError:
        pytest.skip("pwsh is not installed on this host")
    assert isinstance(ok, ToolResult)
    assert ok.data.kind is CommandKind.POWERSHELL
    with pytest.raises(InvalidExecutionRequestError):
        tool.execute(InspectPowerShellRequest("Get-ChildItem", timeout_seconds=11))

    elevated = InspectPowerShellTool(
        paths=PathGuard(tmp_path),
        policy=DeterministicPolicyEngine(
            _config(tmp_path, elevated_session=True, allow_elevated=False)
        ),
        analyzer=PowerShellAstAnalyzer(),
        config=_config(tmp_path, elevated_session=True, allow_elevated=False),
    )
    with pytest.raises(ExecutionElevatedSessionError):
        elevated.execute(InspectPowerShellRequest("Get-ChildItem"))


def test_python_api_inspect_powershell_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from code_harness.domain.errors import (
        ExecutionApprovalRequiredError,
        ExecutionNotSupportedError,
        ExecutionPolicyDeniedError,
    )
    from code_harness.interfaces.serialization import serialize_error

    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    harness = CodeHarness.open(tmp_path)
    try:
        result = harness.inspect_powershell(
            "Get-ChildItem",
            requested_capabilities=("process_spawn",),
        )
    except PowerShellUnavailableError:
        result = None
    if result is None:
        return
    assert isinstance(result, ToolResult)
    assert result.data.decision is PolicyDecision.APPROVAL_REQUIRED

    for error in (
        ExecutionNotSupportedError("backend missing", backend="windows_sandbox"),
        ExecutionPolicyDeniedError("denied", decision="deny"),
        ExecutionApprovalRequiredError("need approval", digest="abc"),
        InvalidExecutionRequestError("bad", field="cwd"),
        ExecutionElevatedSessionError(),
    ):
        payload = serialize_error(error)
        assert payload["error"]["capability"] == "execution"
        assert payload["error"]["code"]


def test_powershell_ast_analysis_detects_dynamic_features_without_execution(
    tmp_path: Path,
) -> None:
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer

    marker = tmp_path / "must-not-exist.txt"
    script = f"New-Item -ItemType File -Path '{marker}'; IEX $command"
    try:
        analysis = PowerShellAstAnalyzer().analyze(script, timeout_seconds=5)
    except PowerShellUnavailableError:
        pytest.skip("pwsh is not installed on this host")

    assert "invoke_expression" in analysis.dynamic_features
    assert not marker.exists()


def test_powershell_ast_analysis_reports_unavailable() -> None:
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer

    with pytest.raises(PowerShellUnavailableError):
        PowerShellAstAnalyzer("missing-pwsh-for-code-harness").analyze(
            "Get-ChildItem", timeout_seconds=5
        )


def test_powershell_ast_analysis_reports_parse_errors() -> None:
    from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer

    try:
        with pytest.raises(PowerShellParseError):
            PowerShellAstAnalyzer().analyze("if (", timeout_seconds=5)
    except PowerShellUnavailableError:
        pytest.skip("pwsh is not installed on this host")


def test_inspection_reports_e0_backend_guarantees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    container = build_container(Settings.for_root(tmp_path))
    assert container.execution is not None

    result = container.execution.inspect_process.execute(InspectProcessRequest("git", ("status",)))

    guarantees = result.data.backend_guarantees
    assert guarantees.backend == "host_supervised"
    assert guarantees.execution_available is (os.name == "nt")
    assert guarantees.process_tree_containment is (os.name == "nt")
    assert guarantees.filesystem_isolated is False


def test_settings_execution_validation(tmp_path: Path) -> None:
    index = tmp_path / "index.db"
    with pytest.raises(ValueError, match="execution_backend"):
        Settings(root=tmp_path, index_path=index, execution_backend="cloud")
    with pytest.raises(ValueError, match="execution_backend"):
        Settings(root=tmp_path, index_path=index, execution_backend="host")
    with pytest.raises(ValueError, match="execution_default_timeout_seconds"):
        Settings(root=tmp_path, index_path=index, execution_default_timeout_seconds=0)
    with pytest.raises(ValueError, match="execution_max_timeout_seconds"):
        Settings(root=tmp_path, index_path=index, execution_max_timeout_seconds=0)
    with pytest.raises(ValueError, match="must not exceed"):
        Settings(
            root=tmp_path,
            index_path=index,
            execution_default_timeout_seconds=100,
            execution_max_timeout_seconds=10,
        )
    with pytest.raises(ValueError, match="execution_max_output_bytes"):
        Settings(root=tmp_path, index_path=index, execution_max_output_bytes=0)
