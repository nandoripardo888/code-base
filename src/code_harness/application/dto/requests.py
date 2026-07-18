from dataclasses import dataclass


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class ListFilesRequest:
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_results: int = 10_000

    def __post_init__(self) -> None:
        _require_positive("max_results", self.max_results)


@dataclass(frozen=True, slots=True)
class SearchFilesRequest:
    query: str
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_results: int = 50
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)


@dataclass(frozen=True, slots=True)
class _SearchContentRequest:
    query: str
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_results: int = 50
    context_lines: int = 0
    case_sensitive: bool = False
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.query:
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)
        _require_positive("timeout_seconds", self.timeout_seconds)
        if self.context_lines < 0:
            raise ValueError("context_lines must be zero or greater")


@dataclass(frozen=True, slots=True)
class SearchTextRequest(_SearchContentRequest):
    pass


@dataclass(frozen=True, slots=True)
class SearchRegexRequest(_SearchContentRequest):
    pass


@dataclass(frozen=True, slots=True)
class ReadFileRequest:
    path: str
    max_chars: int = 200_000
    max_lines: int = 5_000

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path must not be empty")
        _require_positive("max_chars", self.max_chars)
        _require_positive("max_lines", self.max_lines)


@dataclass(frozen=True, slots=True)
class ReadRangeRequest:
    path: str
    start_line: int
    end_line: int
    max_chars: int = 200_000

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path must not be empty")
        _require_positive("start_line", self.start_line)
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        _require_positive("max_chars", self.max_chars)
