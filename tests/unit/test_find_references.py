from dataclasses import dataclass

import pytest

from code_harness.application.dto.requests import FindReferencesRequest
from code_harness.application.tools.structural import FindReferencesTool
from code_harness.domain.enums import CapabilityState, ErrorCode, IndexState
from code_harness.domain.errors import CodeHarnessError, IndexCorruptedError, RipgrepUnavailableError
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import IndexedSource, IndexStatus
from code_harness.domain.models.project import Project
from code_harness.domain.models.search_hit import SearchHit, SearchOutcome
from code_harness.domain.models.structural import CodeReference, StructuralSearchResult
from code_harness.domain.models.tool_result import warning_message

@dataclass(frozen=True)
class FakeStatus:
    state: IndexState = IndexState.READY
    structural_schema_ready: bool = True


class FakeStore:
    def __init__(
        self,
        *,
        references: tuple[StructuralSearchResult, ...] = (),
        symbols: tuple[StructuralSearchResult, ...] = (),
        status: FakeStatus | None = None,
        error: Exception | None = None,
    ) -> None:
        self._references = references
        self._symbols = symbols
        self._status = status or FakeStatus()
        self._error = error
        self.last_reference_query: str | None = None

    def get_status(self, project: Project) -> FakeStatus:
        return self._status

    def find_symbols(
        self, project_id: str, query: str, *, exact: bool, limit: int
    ) -> tuple[StructuralSearchResult, ...]:
        folded = query.casefold()
        matches = []
        for item in self._symbols:
            symbol = item.symbol
            if symbol is None:
                continue
            names = {symbol.name.casefold(), (symbol.qualified_name or "").casefold()}
            if exact:
                if folded in names:
                    matches.append(item)
            elif any(folded in name for name in names):
                matches.append(item)
        return tuple(matches[:limit])

    def find_symbols_by_ids(
        self, project_id: str, symbol_ids: tuple[str, ...]
    ) -> tuple[StructuralSearchResult, ...]:
        wanted = set(symbol_ids)
        return tuple(
            item
            for item in self._symbols
            if item.symbol is not None and item.symbol.symbol_id in wanted
        )

    def get_outline(
        self, project_id: str, path: str
    ) -> tuple[StructuralSearchResult, ...]:
        return tuple(
            item
            for item in self._symbols
            if item.symbol is not None and item.symbol.location.path == path
        )

    def find_references(
        self, project_id: str, target_name: str, *, limit: int
    ) -> tuple[StructuralSearchResult, ...]:
        if self._error is not None:
            raise self._error
        self.last_reference_query = target_name
        return tuple(
            item
            for item in self._references
            if item.reference is not None
            and item.reference.target_name.casefold() == target_name.casefold()
        )[:limit]


class FakeReader:
    def __init__(self, sources: dict[str, IndexedSource]) -> None:
        self._sources = sources

    def load(self, path: str) -> IndexedSource:
        return self._sources[path]


class FakeLexical:
    def __init__(
        self,
        *,
        hits: tuple[SearchHit, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._hits = hits
        self._error = error
        self.last_query: str | None = None

    def search(self, **kwargs: object) -> SearchOutcome:
        if self._error is not None:
            raise self._error
        self.last_query = str(kwargs.get("query"))
        return SearchOutcome(self._hits)


def _source(path: str, content: str, content_hash: str = "hash") -> IndexedSource:
    return IndexedSource(
        path=path,
        content=content,
        size_bytes=len(content.encode()),
        modified_at_ns=1,
        language="java",
        encoding="utf-8",
        content_hash=content_hash,
    )


def _structural_ref(
    *,
    path: str = "src/Caller.java",
    line: int = 2,
    content: str = "    service.validarAgenda();\n",
    content_hash: str = "hash",
) -> StructuralSearchResult:
    location = CodeLocation(path, line, line)
    return StructuralSearchResult(
        None,
        CodeReference("ref1", "validarAgenda", "call", location, source="structural"),
        "",
        content_hash,
    )


def _lexical_hit(
    *,
    path: str = "src/Other.java",
    line: int = 4,
    content: str = "validarAgenda();\n",
    content_hash: str = "hash-other",
) -> SearchHit:
    from code_harness.domain.models.code_chunk import CodeSnippet
    from code_harness.domain.enums import MatchType

    location = CodeLocation(path, line, line)
    return SearchHit(
        CodeSnippet(location, content, "java", content_hash),
        1.0,
        MatchType.EXACT,
        ("validarAgenda",),
        "literal match",
    )


def test_find_references_returns_structural_when_ripgrep_unavailable() -> None:
    content = "class Caller {\n    service.validarAgenda();\n}\n"
    source = _source("src/Caller.java", content)
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(references=(_structural_ref(),)),  # type: ignore[arg-type]
        FakeReader({"src/Caller.java": source}),
        FakeLexical(error=RipgrepUnavailableError("rg")),
    )

    result = tool.execute(FindReferencesRequest("validarAgenda"))

    assert len(result.data) == 1
    assert result.data[0].reference is not None
    assert result.data[0].reference.source == "structural"
    assert result.data[0].reference.validated is True
    assert any(outcome.strategy == "ripgrep" for outcome in result.strategies)
    assert any(
        outcome.state is CapabilityState.UNAVAILABLE for outcome in result.strategies
    )
    assert any("Ripgrep" in warning_message(warning) for warning in result.warnings)


def test_find_references_returns_lexical_when_structural_unavailable() -> None:
    content = "validarAgenda();\n"
    source = _source("src/Other.java", content, "hash-other")
    hit = _lexical_hit()
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(status=FakeStatus(structural_schema_ready=False)),  # type: ignore[arg-type]
        FakeReader({"src/Other.java": source}),
        FakeLexical(hits=(hit,)),
    )

    result = tool.execute(FindReferencesRequest("validarAgenda"))

    assert len(result.data) == 1
    assert result.data[0].reference is not None
    assert result.data[0].reference.kind == "unknown_textual"
    assert result.data[0].reference.source == "lexical"
    assert any("lexical" in warning_message(warning).casefold() for warning in result.warnings)


def test_find_references_raises_when_both_strategies_unavailable() -> None:
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(status=FakeStatus(structural_schema_ready=False)),  # type: ignore[arg-type]
        FakeReader({}),
        FakeLexical(error=RipgrepUnavailableError("rg")),
    )

    with pytest.raises(CodeHarnessError) as raised:
        tool.execute(FindReferencesRequest("validarAgenda"))

    assert raised.value.code is ErrorCode.RIPGREP_UNAVAILABLE
    assert raised.value.recoverable is True


def test_find_references_propagates_non_recoverable_structural_error() -> None:
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(error=IndexCorruptedError("broken db")),  # type: ignore[arg-type]
        FakeReader({}),
        FakeLexical(),
    )

    with pytest.raises(IndexCorruptedError):
        tool.execute(FindReferencesRequest("validarAgenda"))


def test_find_references_resolves_qualified_name_to_simple_target() -> None:
    from code_harness.domain.models.structural import CodeSymbol

    content = (
        "class FrmTestePessoa {\n"
        "    void executar() {\n"
        "        carregarListagem();\n"
        "    }\n"
        "    private void carregarListagem() {}\n"
        "}\n"
    )
    source = _source("src/FrmTestePessoa.java", content)
    owner = CodeSymbol(
        "owner1",
        "FrmTestePessoa",
        "FrmTestePessoa",
        "class",
        CodeLocation("src/FrmTestePessoa.java", 1, 6),
    )
    symbol = CodeSymbol(
        "sym1",
        "carregarListagem",
        "FrmTestePessoa.carregarListagem",
        "method",
        CodeLocation("src/FrmTestePessoa.java", 5, 5),
        signature="private void carregarListagem()",
        parent_symbol_id="owner1",
    )
    location = CodeLocation("src/FrmTestePessoa.java", 3, 3)
    structural = StructuralSearchResult(
        None,
        CodeReference("ref1", "carregarListagem", "call", location, source="structural"),
        "",
        "hash",
    )
    store = FakeStore(
        references=(structural,),
        symbols=(
            StructuralSearchResult(owner, None, "", "hash"),
            StructuralSearchResult(symbol, None, "", "hash"),
        ),
    )
    lexical = FakeLexical()
    tool = FindReferencesTool(
        Project("p", "root"),
        store,  # type: ignore[arg-type]
        FakeReader({"src/FrmTestePessoa.java": source}),
        lexical,
    )

    result = tool.execute(FindReferencesRequest("FrmTestePessoa.carregarListagem"))

    assert store.last_reference_query == "carregarListagem"
    assert lexical.last_query == "carregarListagem"
    assert len(result.data) == 1
    assert result.data[0].reference is not None
    assert result.data[0].reference.target_symbol_id == "sym1"
    assert result.data[0].reference.resolution == "symbol_id"
    assert result.data[0].reference.confidence == 1.0


def test_find_references_qualified_excludes_private_homonyms_in_other_classes() -> None:
    from code_harness.domain.models.structural import CodeSymbol

    content = (
        "class A {\n"
        "    private void carregar() {}\n"
        "    void executar() { carregar(); }\n"
        "}\n"
        "class B {\n"
        "    private void carregar() {}\n"
        "    void executar() { carregar(); }\n"
        "}\n"
        "class C {\n"
        "    private void carregar() {}\n"
        "    void executar() { carregar(); }\n"
        "}\n"
    )
    source = _source("src/Demo.java", content)
    class_a = CodeSymbol(
        "classA", "A", "A", "class", CodeLocation("src/Demo.java", 1, 4)
    )
    class_b = CodeSymbol(
        "classB", "B", "B", "class", CodeLocation("src/Demo.java", 5, 8)
    )
    class_c = CodeSymbol(
        "classC", "C", "C", "class", CodeLocation("src/Demo.java", 9, 12)
    )
    method_a = CodeSymbol(
        "methodA",
        "carregar",
        "A.carregar",
        "method",
        CodeLocation("src/Demo.java", 2, 2),
        signature="private void carregar()",
        parent_symbol_id="classA",
    )
    method_b = CodeSymbol(
        "methodB",
        "carregar",
        "B.carregar",
        "method",
        CodeLocation("src/Demo.java", 6, 6),
        signature="private void carregar()",
        parent_symbol_id="classB",
    )
    method_c = CodeSymbol(
        "methodC",
        "carregar",
        "C.carregar",
        "method",
        CodeLocation("src/Demo.java", 10, 10),
        signature="private void carregar()",
        parent_symbol_id="classC",
    )
    refs = (
        StructuralSearchResult(
            None,
            CodeReference(
                "refA",
                "carregar",
                "call",
                CodeLocation("src/Demo.java", 3, 3),
                source="structural",
            ),
            "",
            "hash",
        ),
        StructuralSearchResult(
            None,
            CodeReference(
                "refB",
                "carregar",
                "call",
                CodeLocation("src/Demo.java", 7, 7),
                source="structural",
            ),
            "",
            "hash",
        ),
        StructuralSearchResult(
            None,
            CodeReference(
                "refC",
                "carregar",
                "call",
                CodeLocation("src/Demo.java", 11, 11),
                source="structural",
            ),
            "",
            "hash",
        ),
    )
    store = FakeStore(
        references=refs,
        symbols=(
            StructuralSearchResult(class_a, None, "", "hash"),
            StructuralSearchResult(class_b, None, "", "hash"),
            StructuralSearchResult(class_c, None, "", "hash"),
            StructuralSearchResult(method_a, None, "", "hash"),
            StructuralSearchResult(method_b, None, "", "hash"),
            StructuralSearchResult(method_c, None, "", "hash"),
        ),
    )
    tool = FindReferencesTool(
        Project("p", "root"),
        store,  # type: ignore[arg-type]
        FakeReader({"src/Demo.java": source}),
        FakeLexical(),
    )

    result = tool.execute(FindReferencesRequest("A.carregar"))

    assert len(result.data) == 1
    assert result.data[0].reference is not None
    assert result.data[0].reference.location.start_line == 3
    assert result.data[0].reference.target_symbol_id == "methodA"
    assert result.data[0].reference.resolution == "symbol_id"


def test_find_references_never_uses_symbol_id_resolution_without_target() -> None:
    content = "class Caller {\n    service.validarAgenda();\n}\n"
    source = _source("src/Caller.java", content)
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(references=(_structural_ref(),)),  # type: ignore[arg-type]
        FakeReader({"src/Caller.java": source}),
        FakeLexical(),
    )

    result = tool.execute(FindReferencesRequest("validarAgenda"))

    assert result.data[0].reference is not None
    assert result.data[0].reference.target_symbol_id is None
    assert result.data[0].reference.resolution == "name_only"
    assert result.data[0].reference.confidence < 1.0


def test_find_references_marks_lexical_definition_for_resolved_symbol() -> None:
    from code_harness.domain.enums import MatchType
    from code_harness.domain.models.code_chunk import CodeSnippet
    from code_harness.domain.models.structural import CodeSymbol

    definition_path = "src/FrmTestePessoa.java"
    definition_content = (
        "class FrmTestePessoa {\n    private void carregarListagem() {\n        return;\n    }\n}\n"
    )
    source = _source(definition_path, definition_content)
    owner = CodeSymbol(
        "owner1",
        "FrmTestePessoa",
        "FrmTestePessoa",
        "class",
        CodeLocation(definition_path, 1, 4),
    )
    symbol = CodeSymbol(
        "sym1",
        "carregarListagem",
        "FrmTestePessoa.carregarListagem",
        "method",
        CodeLocation(definition_path, 2, 4),
        signature="private void carregarListagem()",
        parent_symbol_id="owner1",
    )
    hit = SearchHit(
        CodeSnippet(
            CodeLocation(definition_path, 2, 2),
            "    private void carregarListagem() {\n",
            "java",
            "hash",
        ),
        1.0,
        MatchType.EXACT,
        ("carregarListagem",),
        "literal match",
    )
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(
            symbols=(
                StructuralSearchResult(owner, None, "", "hash"),
                StructuralSearchResult(symbol, None, "", "hash"),
            )
        ),  # type: ignore[arg-type]
        FakeReader({definition_path: source}),
        FakeLexical(hits=(hit,)),
    )

    result = tool.execute(FindReferencesRequest("FrmTestePessoa.carregarListagem"))

    assert any(
        item.reference is not None and item.reference.kind == "definition"
        for item in result.data
    )


def test_find_references_marks_definition_for_simple_name_query() -> None:
    from code_harness.domain.enums import MatchType
    from code_harness.domain.models.code_chunk import CodeSnippet
    from code_harness.domain.models.structural import CodeSymbol

    path = "src/Demo.java"
    content = "class Demo {\n    private void carregar() {}\n}\n"
    source = _source(path, content)
    symbol = CodeSymbol(
        "sym1",
        "carregar",
        "Demo.carregar",
        "method",
        CodeLocation(path, 2, 2),
        signature="private void carregar()",
    )
    hit = SearchHit(
        CodeSnippet(
            CodeLocation(path, 2, 2),
            "    private void carregar() {}\n",
            "java",
            "hash",
        ),
        1.0,
        MatchType.EXACT,
        ("carregar",),
        "literal match",
    )
    tool = FindReferencesTool(
        Project("p", "root"),
        FakeStore(symbols=(StructuralSearchResult(symbol, None, "", "hash"),)),  # type: ignore[arg-type]
        FakeReader({path: source}),
        FakeLexical(hits=(hit,)),
    )

    result = tool.execute(FindReferencesRequest("carregar"))

    assert result.data[0].reference is not None
    assert result.data[0].reference.kind == "definition"
