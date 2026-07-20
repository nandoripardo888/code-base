from dataclasses import dataclass

from code_harness.domain.enums import IndexMode


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
class SemanticSearchRequest:
    query: str
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    max_results: int = 50

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)


@dataclass(frozen=True, slots=True)
class SearchCodeRequest:
    query: str
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    max_results: int = 50
    context_lines: int = 2
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)
        _require_positive("timeout_seconds", self.timeout_seconds)
        if self.context_lines < 0:
            raise ValueError("context_lines must be zero or greater")


@dataclass(frozen=True, slots=True)
class BuildContextRequest:
    query: str
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    max_tokens: int = 12_000
    reserved_tokens: int = 0
    max_files: int = 12
    max_snippets: int = 20
    max_expansion_depth: int = 2

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_tokens", self.max_tokens)
        _require_positive("max_files", self.max_files)
        _require_positive("max_snippets", self.max_snippets)
        if self.reserved_tokens < 0 or self.reserved_tokens >= self.max_tokens:
            raise ValueError("reserved_tokens must be non-negative and smaller than max_tokens")
        if self.max_expansion_depth < 0:
            raise ValueError("max_expansion_depth must be zero or greater")


@dataclass(frozen=True, slots=True)
class GetRepositoryMapRequest:
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    max_files: int = 200
    max_symbols_per_file: int = 10

    def __post_init__(self) -> None:
        _require_positive("max_files", self.max_files)
        _require_positive("max_symbols_per_file", self.max_symbols_per_file)


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


@dataclass(frozen=True, slots=True)
class IndexProjectRequest:
    mode: IndexMode = IndexMode.INCREMENTAL


@dataclass(frozen=True, slots=True)
class GetFileOutlineRequest:
    path: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path must not be empty")


@dataclass(frozen=True, slots=True)
class FindSymbolRequest:
    query: str
    max_results: int = 50
    exact: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)


@dataclass(frozen=True, slots=True)
class FindDefinitionRequest:
    query: str
    max_results: int = 20

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)


@dataclass(frozen=True, slots=True)
class FindReferencesRequest:
    query: str
    max_results: int = 100
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)
        _require_positive("timeout_seconds", self.timeout_seconds)
