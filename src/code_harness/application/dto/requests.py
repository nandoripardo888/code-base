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
    cursor: str | None = None
    sort: str = "path"
    sort_direction: str = "asc"
    include_total_count: bool = True

    def __post_init__(self) -> None:
        _require_positive("max_results", self.max_results)
        if self.sort not in {"path", "size", "mtime", "language"}:
            raise ValueError("sort must be one of: path, size, mtime, language")
        if self.sort_direction not in {"asc", "desc"}:
            raise ValueError("sort_direction must be 'asc' or 'desc'")


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
    mode: str = "detailed"
    path: str | None = None
    max_depth: int | None = None
    cursor: str | None = None
    include_files: bool = True
    include_symbols: bool | None = None
    collapse_single_child_directories: bool = False

    def __post_init__(self) -> None:
        _require_positive("max_files", self.max_files)
        _require_positive("max_symbols_per_file", self.max_symbols_per_file)
        if self.mode not in {"summary", "files", "detailed"}:
            raise ValueError("mode must be one of: summary, files, detailed")
        if self.max_depth is not None:
            _require_positive("max_depth", self.max_depth)

    @property
    def effective_include_symbols(self) -> bool:
        if self.include_symbols is not None:
            return self.include_symbols
        return self.mode == "detailed"


@dataclass(frozen=True, slots=True)
class ReadFileRequest:
    path: str
    max_chars: int = 200_000
    max_lines: int = 5_000
    include_line_numbers: bool = False

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
    include_line_numbers: bool = False

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
    include_content: bool | None = None
    response_format: str = "compact"
    include_signatures: bool = True
    max_symbols: int | None = None
    max_depth: int | None = None
    symbol_kinds: tuple[str, ...] = ()
    max_content_chars_per_symbol: int | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path must not be empty")
        if self.response_format not in {"compact", "full"}:
            raise ValueError("response_format must be 'compact' or 'full'")
        if self.max_symbols is not None:
            _require_positive("max_symbols", self.max_symbols)
        if self.max_depth is not None:
            _require_positive("max_depth", self.max_depth)
        if self.max_content_chars_per_symbol is not None:
            _require_positive(
                "max_content_chars_per_symbol", self.max_content_chars_per_symbol
            )

    @property
    def effective_include_content(self) -> bool:
        if self.include_content is not None:
            return self.include_content
        return self.response_format == "full"


@dataclass(frozen=True, slots=True)
class FindSymbolRequest:
    query: str
    max_results: int = 50
    exact: bool = False
    include_content: bool | None = None
    response_format: str = "compact"
    max_content_chars_per_symbol: int | None = None
    kind: str | None = None
    path: str | None = None
    language: str | None = None
    parameter_count: int | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        _require_positive("max_results", self.max_results)
        if self.response_format not in {"compact", "full"}:
            raise ValueError("response_format must be 'compact' or 'full'")
        if self.max_content_chars_per_symbol is not None:
            _require_positive(
                "max_content_chars_per_symbol", self.max_content_chars_per_symbol
            )
        if self.parameter_count is not None and self.parameter_count < 0:
            raise ValueError("parameter_count must be zero or greater")

    @property
    def effective_include_content(self) -> bool:
        if self.include_content is not None:
            return self.include_content
        return self.response_format == "full"


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
