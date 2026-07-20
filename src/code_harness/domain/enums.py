from enum import StrEnum


class MatchType(StrEnum):
    EXACT = "exact"
    REGEX = "regex"
    FULL_TEXT = "full_text"
    SYMBOL = "symbol"
    REFERENCE = "reference"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    PATH = "path"


class QueryKind(StrEnum):
    EXACT = "exact"
    MIXED = "mixed"
    CONCEPTUAL = "conceptual"


class IndexMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    VERIFY = "verify"


class IndexState(StrEnum):
    NOT_INITIALIZED = "not_initialized"
    INDEXING = "indexing"
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    FAILED = "failed"
    REPAIRING = "repairing"


class ParseState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    READY = "ready"
    FALLBACK = "fallback"
    FAILED = "failed"


class DiagnosticStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class ErrorCode(StrEnum):
    PROJECT_NOT_FOUND = "project_not_found"
    PATH_OUTSIDE_PROJECT = "path_outside_project"
    FILE_NOT_FOUND = "file_not_found"
    BINARY_FILE = "binary_file"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    RIPGREP_UNAVAILABLE = "ripgrep_unavailable"
    INDEX_NOT_READY = "index_not_ready"
    INDEX_CORRUPTED = "index_corrupted"
    PARSER_UNAVAILABLE = "parser_unavailable"
    PARSER_TIMEOUT = "parser_timeout"
    PARSER_CRASH = "parser_crash"
    PARSER_CIRCUIT_OPEN = "parser_circuit_open"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    INVALID_QUERY = "invalid_query"
    RESULT_LIMIT_EXCEEDED = "result_limit_exceeded"
