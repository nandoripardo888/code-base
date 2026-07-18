from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CodeLocation:
    path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith(("/", "\\")):
            raise ValueError("path must be a non-empty relative path")
        if self.start_line < 1:
            raise ValueError("start_line must be at least 1")
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.start_column is not None and self.start_column < 1:
            raise ValueError("start_column must be at least 1")
        if self.end_column is not None and self.end_column < 1:
            raise ValueError("end_column must be at least 1")
