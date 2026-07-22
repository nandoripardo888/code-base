from dataclasses import dataclass

from code_harness.domain.enums import ParseState
from code_harness.domain.models.code_location import CodeLocation


@dataclass(frozen=True, slots=True)
class CodeSymbol:
    symbol_id: str
    name: str
    qualified_name: str | None
    kind: str
    location: CodeLocation
    signature: str | None = None
    parent_symbol_id: str | None = None
    canonical_signature: str | None = None


@dataclass(frozen=True, slots=True)
class CodeReference:
    reference_id: str
    target_name: str
    kind: str
    location: CodeLocation
    source_symbol_id: str | None = None
    source: str = "structural"
    target_symbol_id: str | None = None
    confidence: float = 1.0
    validated: bool = False
    resolution: str = "name_only"


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    location: CodeLocation
    content: str
    content_hash: str
    kind: str
    symbol_id: str | None = None
    parent_chunk_id: str | None = None


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    request_id: str
    path: str
    language: str
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class AnalyzeResult:
    parser_name: str
    parser_version: str
    state: ParseState
    symbols: tuple[CodeSymbol, ...] = ()
    references: tuple[CodeReference, ...] = ()
    chunks: tuple[CodeChunk, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuralSearchResult:
    symbol: CodeSymbol | None
    reference: CodeReference | None
    content: str | None
    file_hash: str
    content_included: bool = False
