from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolResult[T]:
    data: T
    elapsed_ms: int
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    index_state: str | None = None
