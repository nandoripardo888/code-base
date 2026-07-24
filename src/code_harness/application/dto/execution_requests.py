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
