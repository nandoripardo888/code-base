from __future__ import annotations

import re
from pathlib import PurePath, PureWindowsPath

from code_harness.application.execution.approval_digest import compute_approval_digest, script_hash
from code_harness.domain.enums import (
    CommandKind,
    ExecutionCapability,
    ExecutionRiskSeverity,
    PolicyDecision,
)
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionRuntimeConfig,
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    PolicyReason,
    RiskFinding,
)

_SHELL_EXECUTABLES = frozenset(
    {
        "cmd",
        "cmd.exe",
        "command.com",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "bash",
        "bash.exe",
        "sh",
        "zsh",
        "wscript",
        "wscript.exe",
        "cscript",
        "cscript.exe",
        "mshta",
        "mshta.exe",
    }
)

_AUTOALLOW_PROCESS: frozenset[tuple[str, tuple[str, ...]]] = frozenset(
    {
        ("git", ("status",)),
        ("git", ("status", "--short")),
        ("git", ("diff",)),
        ("git", ("diff", "--stat")),
        ("git", ("log", "-1", "--oneline")),
        ("rg", ("--version",)),
        ("python", ("--version",)),
        ("python3", ("--version",)),
        ("pwsh", ("--version",)),
        ("mvn", ("--version",)),
        ("ant", ("-version",)),
    }
)

_PROTECTED_PATH_MARKERS = (
    ".code-harness",
    ".env",
    ".git/config",
    ".git/hooks",
    "credential",
    "secret",
    ".pem",
    ".key",
)

_POWERSHELL_DYNAMIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invoke_expression", re.compile(r"(?i)\bInvoke-Expression\b|\biex\b")),
    ("call_operator_dynamic", re.compile(r"(?i)&\s*\$")),
    ("dot_source_dynamic", re.compile(r"(?i)^\s*\.\s*\$", re.MULTILINE)),
    ("start_process", re.compile(r"(?i)\bStart-Process\b")),
    ("add_type", re.compile(r"(?i)\bAdd-Type\b")),
    ("encoded_command", re.compile(r"(?i)\bEncodedCommand\b|-enc(?:odedcommand)?\b")),
    ("import_module_dynamic", re.compile(r"(?i)\bImport-Module\b.*\$")),
    ("download_cradle", re.compile(r"(?i)\b(Invoke-WebRequest|Invoke-RestMethod|wget|curl)\b")),
    ("registry", re.compile(r"(?i)\b(HKLM:|HKCU:|Registry::)")),
    (
        "service_control",
        re.compile(r"(?i)\b(Get-Service|Stop-Service|Start-Service|Set-Service)\b"),
    ),
    ("remove_item", re.compile(r"(?i)\bRemove-Item\b")),
)


def _executable_name(executable: str) -> str:
    name = PureWindowsPath(executable).name or PurePath(executable).name or executable
    return name.casefold()


def _unique_capabilities(
    *groups: tuple[ExecutionCapability, ...],
) -> tuple[ExecutionCapability, ...]:
    ordered: list[ExecutionCapability] = []
    seen: set[ExecutionCapability] = set()
    for group in groups:
        for item in group:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
    return tuple(ordered)


class DeterministicPolicyEngine:
    def __init__(self, config: ExecutionRuntimeConfig) -> None:
        self._config = config

    def inspect_process(self, command: NormalizedProcessCommand) -> CommandInspection:
        name = _executable_name(command.executable)
        args = tuple(command.args)
        reasons: list[PolicyReason] = []
        risks: list[RiskFinding] = []
        blocks: list[PolicyReason] = []
        required = list(command.requested_capabilities)
        dynamic: list[str] = []
        protected = _protected_matches((command.executable, *args, command.cwd))

        if self._config.elevated_session and not self._config.allow_elevated:
            blocks.append(
                PolicyReason(
                    "elevated_session",
                    "Host session is elevated and execution_allow_elevated is false.",
                )
            )
            return self._result(
                kind=CommandKind.PROCESS,
                decision=PolicyDecision.DENY,
                command_capabilities=tuple(required),
                required=tuple(required),
                reasons=reasons,
                risks=risks,
                blocks=blocks,
                cwd=command.cwd,
                timeout_seconds=command.timeout_seconds,
                max_output_bytes=command.max_output_bytes,
                executable=command.executable,
                args=args,
                dynamic_features=tuple(dynamic),
                protected_path_matches=protected,
                digest_capabilities=tuple(required),
            )

        autoallow_key = (name.removesuffix(".exe"), args)
        if autoallow_key in _AUTOALLOW_PROCESS:
            required_caps = _unique_capabilities(
                command.requested_capabilities,
                (ExecutionCapability.PROCESS_SPAWN,),
            )
            reasons.append(
                PolicyReason("autoallow", "Command matches the read-only autoallow allowlist.")
            )
            return self._result(
                kind=CommandKind.PROCESS,
                decision=PolicyDecision.ALLOW,
                command_capabilities=command.requested_capabilities,
                required=required_caps,
                reasons=reasons,
                risks=risks,
                blocks=blocks,
                cwd=command.cwd,
                timeout_seconds=command.timeout_seconds,
                max_output_bytes=command.max_output_bytes,
                executable=command.executable,
                args=args,
                dynamic_features=tuple(dynamic),
                protected_path_matches=protected,
                digest_capabilities=required_caps,
            )

        if name in _SHELL_EXECUTABLES or name.endswith((".bat", ".cmd", ".ps1")):
            blocks.append(
                PolicyReason(
                    "shell_or_script_wrapper",
                    "Shell interpreters and script wrappers are not accepted by run_process.",
                    evidence=name,
                )
            )
            risks.append(
                RiskFinding(
                    "shell_execution",
                    ExecutionRiskSeverity.CRITICAL,
                    "Invoking a shell or script host bypasses structured argument execution.",
                    evidence=name,
                )
            )
            required.extend(
                (
                    ExecutionCapability.PROCESS_SPAWN,
                    ExecutionCapability.EXECUTE_REPOSITORY_CODE,
                )
            )
            return self._result(
                kind=CommandKind.PROCESS,
                decision=PolicyDecision.DENY,
                command_capabilities=command.requested_capabilities,
                required=_unique_capabilities(tuple(required)),
                reasons=reasons,
                risks=risks,
                blocks=blocks,
                cwd=command.cwd,
                timeout_seconds=command.timeout_seconds,
                max_output_bytes=command.max_output_bytes,
                executable=command.executable,
                args=args,
                dynamic_features=("shell_wrapper",),
                protected_path_matches=protected,
                digest_capabilities=_unique_capabilities(tuple(required)),
            )

        inferred, infer_reasons, infer_risks = _infer_process_capabilities(name, args)
        required_caps = _unique_capabilities(command.requested_capabilities, inferred)
        reasons.extend(infer_reasons)
        risks.extend(infer_risks)
        classified = _is_classified_executable(name, args)

        if protected:
            risks.append(
                RiskFinding(
                    "protected_path",
                    ExecutionRiskSeverity.HIGH,
                    "Arguments or cwd reference protected paths.",
                    evidence=", ".join(protected),
                )
            )
            decision = PolicyDecision.DENY
            blocks.append(
                PolicyReason(
                    "protected_path",
                    "Command references protected policy, secrets, or harness paths.",
                    evidence=", ".join(protected),
                )
            )
        elif _is_hard_deny_git(name, args):
            decision = PolicyDecision.DENY
            blocks.append(
                PolicyReason(
                    "destructive_git",
                    "Destructive or publishing git operations are denied by default.",
                    evidence=" ".join((name, *args)),
                )
            )
            risks.append(
                RiskFinding(
                    "git_destructive",
                    ExecutionRiskSeverity.CRITICAL,
                    "Git command can destroy work or publish credentials.",
                )
            )
        elif name in {"pytest", "python", "python3", "mvn", "npm", "npx", "node", "ant"}:
            decision = PolicyDecision.APPROVAL_REQUIRED
            reasons.append(
                PolicyReason(
                    "repository_code",
                    "Build/test tooling can execute repository code and requires approval.",
                )
            )
            risks.append(
                RiskFinding(
                    "execute_repository_code",
                    ExecutionRiskSeverity.HIGH,
                    "Tooling may load and run project code, plugins, or scripts.",
                )
            )
        elif not classified:
            decision = PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
            dynamic.append("unclassified_executable")
            reasons.append(
                PolicyReason(
                    "unclassified_executable",
                    "Executable is not covered by deterministic rules.",
                    evidence=name,
                )
            )
            risks.append(
                RiskFinding(
                    "unknown_executable",
                    ExecutionRiskSeverity.HIGH,
                    "Unknown executables are never auto-approved.",
                )
            )
        else:
            decision = PolicyDecision.APPROVAL_REQUIRED
            reasons.append(
                PolicyReason(
                    "approval_required",
                    "Command requires explicit host approval under the current policy.",
                )
            )

        if (
            decision is PolicyDecision.ALLOW
            and self._config.require_approval
            and ExecutionCapability.NETWORK_OUTBOUND in required_caps
        ):
            decision = PolicyDecision.APPROVAL_REQUIRED

        return self._result(
            kind=CommandKind.PROCESS,
            decision=decision,
            command_capabilities=command.requested_capabilities,
            required=required_caps,
            reasons=reasons,
            risks=risks,
            blocks=blocks,
            cwd=command.cwd,
            timeout_seconds=command.timeout_seconds,
            max_output_bytes=command.max_output_bytes,
            executable=command.executable,
            args=args,
            dynamic_features=tuple(dynamic),
            protected_path_matches=protected,
            digest_capabilities=required_caps,
        )

    def inspect_powershell(self, command: NormalizedPowerShellCommand) -> CommandInspection:
        reasons: list[PolicyReason] = []
        risks: list[RiskFinding] = []
        blocks: list[PolicyReason] = []
        dynamic: list[str] = []
        protected = _protected_matches((command.script, command.cwd))
        required = list(command.requested_capabilities)
        required.extend(
            (
                ExecutionCapability.PROCESS_SPAWN,
                ExecutionCapability.EXECUTE_REPOSITORY_CODE,
            )
        )

        if self._config.elevated_session and not self._config.allow_elevated:
            blocks.append(
                PolicyReason(
                    "elevated_session",
                    "Host session is elevated and execution_allow_elevated is false.",
                )
            )
            return self._result(
                kind=CommandKind.POWERSHELL,
                decision=PolicyDecision.DENY,
                command_capabilities=command.requested_capabilities,
                required=_unique_capabilities(tuple(required)),
                reasons=reasons,
                risks=risks,
                blocks=blocks,
                cwd=command.cwd,
                timeout_seconds=command.timeout_seconds,
                max_output_bytes=command.max_output_bytes,
                script_hash_value=script_hash(command.script),
                dynamic_features=tuple(dynamic),
                protected_path_matches=protected,
                digest_capabilities=_unique_capabilities(tuple(required)),
                script=command.script,
            )

        for feature, pattern in _POWERSHELL_DYNAMIC_PATTERNS:
            if pattern.search(command.script):
                dynamic.append(feature)
                risks.append(
                    RiskFinding(
                        feature,
                        ExecutionRiskSeverity.HIGH,
                        f"PowerShell script matches dynamic/risky pattern: {feature}.",
                        evidence=feature,
                    )
                )

        if "encoded_command" in dynamic or "invoke_expression" in dynamic:
            decision = PolicyDecision.DENY
            blocks.append(
                PolicyReason(
                    "powershell_hard_deny",
                    "EncodedCommand and Invoke-Expression are denied.",
                )
            )
        elif "registry" in dynamic:
            required.append(ExecutionCapability.REGISTRY_WRITE)
            decision = PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
        elif "service_control" in dynamic:
            required.append(ExecutionCapability.SERVICE_CONTROL)
            decision = PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
        elif "download_cradle" in dynamic:
            required.append(ExecutionCapability.NETWORK_OUTBOUND)
            decision = PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
        elif dynamic:
            decision = PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR
            reasons.append(
                PolicyReason(
                    "dynamic_powershell",
                    "Script contains dynamic features; never auto-allowed on the host backend.",
                )
            )
        elif protected:
            decision = PolicyDecision.DENY
            blocks.append(
                PolicyReason(
                    "protected_path",
                    "Script references protected paths.",
                    evidence=", ".join(protected),
                )
            )
        else:
            # Free PowerShell always requires approval on host (E0 inspection only).
            decision = PolicyDecision.APPROVAL_REQUIRED
            reasons.append(
                PolicyReason(
                    "powershell_host_approval",
                    "Free PowerShell always requires host approval; inspection does not approve.",
                )
            )
            risks.append(
                RiskFinding(
                    "powershell_surface",
                    ExecutionRiskSeverity.MEDIUM,
                    "PowerShell can reach filesystem, network, and host APIs even with NoProfile.",
                )
            )

        required_caps = _unique_capabilities(tuple(required))
        return self._result(
            kind=CommandKind.POWERSHELL,
            decision=decision,
            command_capabilities=command.requested_capabilities,
            required=required_caps,
            reasons=reasons,
            risks=risks,
            blocks=blocks,
            cwd=command.cwd,
            timeout_seconds=command.timeout_seconds,
            max_output_bytes=command.max_output_bytes,
            script_hash_value=script_hash(command.script),
            dynamic_features=tuple(dynamic),
            protected_path_matches=protected,
            digest_capabilities=required_caps,
            script=command.script,
            warnings=(
                "PowerShell analysis in E0 is superficial pattern matching; it does not parse AST.",
            ),
        )

    def _result(
        self,
        *,
        kind: CommandKind,
        decision: PolicyDecision,
        command_capabilities: tuple[ExecutionCapability, ...],
        required: tuple[ExecutionCapability, ...],
        reasons: list[PolicyReason],
        risks: list[RiskFinding],
        blocks: list[PolicyReason],
        cwd: str,
        timeout_seconds: float,
        max_output_bytes: int,
        digest_capabilities: tuple[ExecutionCapability, ...],
        executable: str | None = None,
        args: tuple[str, ...] = (),
        script: str | None = None,
        script_hash_value: str | None = None,
        dynamic_features: tuple[str, ...] = (),
        protected_path_matches: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> CommandInspection:
        approval_required = decision in {
            PolicyDecision.APPROVAL_REQUIRED,
            PolicyDecision.UNKNOWN_DYNAMIC_BEHAVIOR,
        }
        digest = None
        if decision is not PolicyDecision.DENY and decision is not PolicyDecision.UNSUPPORTED:
            digest = compute_approval_digest(
                project_id=self._config.project_id,
                kind=kind,
                executable=executable,
                args=args,
                script=script,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                capabilities=digest_capabilities,
                backend=self._config.backend,
                policy_version=self._config.policy_version,
                policy_name=self._config.policy_name,
            )
        return CommandInspection(
            kind=kind,
            decision=decision,
            requested_capabilities=command_capabilities,
            required_capabilities=required,
            approval_required=approval_required or decision is PolicyDecision.APPROVAL_REQUIRED,
            reasons=tuple(reasons),
            risks=tuple(risks),
            blocks=tuple(blocks),
            approval_digest=digest,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            executable=executable,
            args=args,
            script_hash=script_hash_value,
            dynamic_features=dynamic_features,
            protected_path_matches=protected_path_matches,
            backend=self._config.backend,
            policy_name=self._config.policy_name,
            policy_version=self._config.policy_version,
            warnings=warnings,
        )


def _infer_process_capabilities(
    name: str,
    args: tuple[str, ...],
) -> tuple[tuple[ExecutionCapability, ...], list[PolicyReason], list[RiskFinding]]:
    reasons: list[PolicyReason] = []
    risks: list[RiskFinding] = []
    caps: list[ExecutionCapability] = [ExecutionCapability.PROCESS_SPAWN]

    if name == "git":
        sub = args[0].casefold() if args else ""
        if sub in {"status", "diff", "log", "show", "ls-files", "rev-parse"}:
            caps.append(ExecutionCapability.GIT_READ)
            reasons.append(PolicyReason("git_read", "Git read-only subcommand."))
        elif sub in {"add", "commit", "checkout", "switch", "restore", "stash", "branch"}:
            caps.extend((ExecutionCapability.GIT_WRITE, ExecutionCapability.WORKSPACE_READ))
            reasons.append(PolicyReason("git_write", "Git write subcommand."))
        elif sub in {"push", "pull", "fetch", "clone"}:
            caps.extend(
                (
                    ExecutionCapability.GIT_WRITE,
                    ExecutionCapability.NETWORK_OUTBOUND,
                    ExecutionCapability.CREDENTIAL_ACCESS,
                )
            )
            risks.append(
                RiskFinding(
                    "git_network",
                    ExecutionRiskSeverity.HIGH,
                    "Git network operations may use credentials and reach remotes.",
                )
            )
        elif sub in {"reset", "clean"}:
            caps.extend(
                (
                    ExecutionCapability.GIT_WRITE,
                    ExecutionCapability.WORKSPACE_WRITE,
                )
            )
            risks.append(
                RiskFinding(
                    "git_destructive",
                    ExecutionRiskSeverity.CRITICAL,
                    "Git reset/clean can destroy local work.",
                )
            )
        else:
            caps.append(ExecutionCapability.GIT_READ)
            reasons.append(
                PolicyReason("git_unknown_subcommand", "Unrecognized git subcommand.", evidence=sub)
            )
    elif name in {"rg", "ripgrep"}:
        caps.append(ExecutionCapability.WORKSPACE_READ)
    elif name in {"pytest"}:
        caps.extend(
            (
                ExecutionCapability.EXECUTE_REPOSITORY_CODE,
                ExecutionCapability.WORKSPACE_READ,
            )
        )
    elif name in {"mvn", "npm", "npx", "ant", "node"}:
        caps.append(ExecutionCapability.EXECUTE_REPOSITORY_CODE)
        if name in {"npm", "npx"} and args and args[0].casefold() in {"install", "ci", "publish"}:
            caps.extend(
                (
                    ExecutionCapability.NETWORK_OUTBOUND,
                    ExecutionCapability.WORKSPACE_WRITE,
                )
            )
    elif name in {"python", "python3"} and args and args[0] not in {"--version", "-V"}:
        caps.append(ExecutionCapability.EXECUTE_REPOSITORY_CODE)

    return tuple(dict.fromkeys(caps)), reasons, risks


def _is_classified_executable(name: str, args: tuple[str, ...]) -> bool:
    if name in {
        "git",
        "rg",
        "ripgrep",
        "pytest",
        "python",
        "python3",
        "mvn",
        "npm",
        "npx",
        "node",
        "ant",
        "pwsh",
        "pwsh.exe",
    }:
        return True
    return (name.removesuffix(".exe"), args) in _AUTOALLOW_PROCESS


def _is_hard_deny_git(name: str, args: tuple[str, ...]) -> bool:
    if name != "git" or not args:
        return False
    sub = args[0].casefold()
    joined = " ".join(args).casefold()
    if sub == "push":
        return True
    if sub == "reset" and "--hard" in joined:
        return True
    return sub == "clean" and ("-fdx" in joined or "-ffdx" in joined)


def _protected_matches(parts: tuple[str, ...]) -> tuple[str, ...]:
    matches: list[str] = []
    for part in parts:
        lowered = part.replace("\\", "/").casefold()
        for marker in _PROTECTED_PATH_MARKERS:
            if marker.casefold() in lowered:
                matches.append(marker)
    return tuple(dict.fromkeys(matches))
