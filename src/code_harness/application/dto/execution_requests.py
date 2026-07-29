from dataclasses import dataclass

from code_harness.domain.enums import ExecutionCapability


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _dedupe_capabilities(
    capabilities: tuple[ExecutionCapability, ...],
) -> tuple[ExecutionCapability, ...]:
    seen: set[ExecutionCapability] = set()
    ordered: list[ExecutionCapability] = []
    for item in capabilities:
        if not isinstance(item, ExecutionCapability):
            item = ExecutionCapability(item)
        if item in seen:
            raise ValueError("requested_capabilities must not contain duplicates")
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class InspectProcessRequest:
    executable: str
    args: tuple[str, ...] = ()
    cwd: str = "."
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    requested_capabilities: tuple[ExecutionCapability, ...] = ()
    reason: str | None = None
    digest_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        stripped = self.executable.strip()
        if not stripped:
            raise ValueError("executable must not be empty")
        looks_like_path = "/" in stripped or "\\" in stripped
        if " " in stripped and not self.args and not looks_like_path:
            raise ValueError(
                "executable must be a single program path; "
                "pass arguments via args, not a shell line"
            )
        if not self.cwd or not self.cwd.strip():
            raise ValueError("cwd must not be empty")
        if self.timeout_seconds is not None:
            _require_positive("timeout_seconds", self.timeout_seconds)
        if self.max_output_bytes is not None:
            _require_positive("max_output_bytes", self.max_output_bytes)
        if len(self.args) > 256:
            raise ValueError("args must not exceed 256 items")
        for index, argument in enumerate(self.args):
            if not isinstance(argument, str):
                raise ValueError(f"args[{index}] must be a string")
            if len(argument) > 16_384:
                raise ValueError(f"args[{index}] exceeds the maximum argument length")
        object.__setattr__(
            self,
            "requested_capabilities",
            _dedupe_capabilities(self.requested_capabilities),
        )
        if self.reason is not None and len(self.reason) > 4_000:
            raise ValueError("reason must not exceed 4000 characters")
        normalized_context: list[tuple[str, str]] = []
        for index, item in enumerate(self.digest_context):
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
            ):
                raise ValueError(f"digest_context[{index}] must be a (str, str) pair")
            key = item[0].strip()
            if not key:
                raise ValueError(f"digest_context[{index}] key must not be empty")
            if len(key) > 128 or len(item[1]) > 16_384:
                raise ValueError(f"digest_context[{index}] exceeds size limits")
            normalized_context.append((key, item[1]))
        object.__setattr__(self, "digest_context", tuple(normalized_context))


@dataclass(frozen=True, slots=True)
class InspectPowerShellRequest:
    script: str
    cwd: str = "."
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    requested_capabilities: tuple[ExecutionCapability, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.script or not self.script.strip():
            raise ValueError("script must not be empty")
        if len(self.script) > 100_000:
            raise ValueError("script must not exceed 100000 characters")
        if not self.cwd or not self.cwd.strip():
            raise ValueError("cwd must not be empty")
        if self.timeout_seconds is not None:
            _require_positive("timeout_seconds", self.timeout_seconds)
        if self.max_output_bytes is not None:
            _require_positive("max_output_bytes", self.max_output_bytes)
        object.__setattr__(
            self,
            "requested_capabilities",
            _dedupe_capabilities(self.requested_capabilities),
        )
        if self.reason is not None and len(self.reason) > 4_000:
            raise ValueError("reason must not exceed 4000 characters")


@dataclass(frozen=True, slots=True)
class RunProcessRequest(InspectProcessRequest):
    """A structured process request, synchronous by default."""

    approval_id: str | None = None
    approval_session_id: str | None = None
    wait: bool = True

    def __post_init__(self) -> None:
        InspectProcessRequest.__post_init__(self)
        if self.approval_id is not None and not self.approval_id.strip():
            raise ValueError("approval_id must not be empty")
        if self.approval_session_id is not None:
            if self.approval_id is None:
                raise ValueError("approval_session_id requires approval_id")
            if not self.approval_session_id.strip():
                raise ValueError("approval_session_id must not be empty")
            if len(self.approval_session_id) > 128:
                raise ValueError("approval_session_id must not exceed 128 characters")
        if not isinstance(self.wait, bool):
            raise ValueError("wait must be a boolean")


@dataclass(frozen=True, slots=True)
class RunPowerShellRequest(InspectPowerShellRequest):
    """A PowerShell 7 execution request, synchronous by default."""

    approval_id: str | None = None
    approval_session_id: str | None = None
    wait: bool = True

    def __post_init__(self) -> None:
        InspectPowerShellRequest.__post_init__(self)
        if self.approval_id is not None and not self.approval_id.strip():
            raise ValueError("approval_id must not be empty")
        if self.approval_session_id is not None:
            if self.approval_id is None:
                raise ValueError("approval_session_id requires approval_id")
            if not self.approval_session_id.strip():
                raise ValueError("approval_session_id must not be empty")
            if len(self.approval_session_id) > 128:
                raise ValueError("approval_session_id must not exceed 128 characters")
        if not isinstance(self.wait, bool):
            raise ValueError("wait must be a boolean")


@dataclass(frozen=True, slots=True)
class GetExecutionRequest:
    execution_id: str
    include_output: bool = True

    def __post_init__(self) -> None:
        if not self.execution_id or not self.execution_id.strip():
            raise ValueError("execution_id must not be empty")
        if not isinstance(self.include_output, bool):
            raise ValueError("include_output must be a boolean")


@dataclass(frozen=True, slots=True)
class TerminateExecutionRequest:
    execution_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.execution_id or not self.execution_id.strip():
            raise ValueError("execution_id must not be empty")
        if self.reason is not None and len(self.reason) > 4_000:
            raise ValueError("reason must not exceed 4000 characters")
