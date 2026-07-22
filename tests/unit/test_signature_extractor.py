from code_harness.infrastructure.parsers.signature_extractor import (
    canonicalize_java,
    canonicalize_python,
    extract_header,
    _strip_java_annotations,
)


def test_extract_header_captures_multiline_java_signature() -> None:
    content = """\
public class Demo {
    public Double criarEnvelope(
        ISession session,
        String nome
    ) throws DataException {
        return 1.0;
    }
}
"""
    header = extract_header(content, 2)
    assert "ISession session" in header
    assert "String nome" in header
    canonical = canonicalize_java(
        qualified_name="Demo.criarEnvelope",
        display_signature=header,
    )
    assert canonical == "Demo.criarEnvelope(ISession,String):Double"


def test_canonicalize_python_uses_annotations() -> None:
    import ast

    source = "def work(session: Session, name: str) -> float:\n    return 1.0\n"
    module = ast.parse(source)
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    canonical = canonicalize_python(
        qualified_name="work",
        args=function.args,
        returns=function.returns,
    )
    assert canonical == "work(Session,str):float"


def test_canonicalize_java_ignores_annotation_arguments() -> None:
    display = '@Command\n@NotifyChange({"selectedCategoria1", "selectedCategoria2"})\npublic void setSelectedCategoria1(TesteCategoria1 value)'
    canonical = canonicalize_java(
        qualified_name="FrmTestePessoa.setSelectedCategoria1",
        display_signature=display,
    )
    assert canonical == "FrmTestePessoa.setSelectedCategoria1(TesteCategoria1):void"


def test_canonicalize_java_ignores_single_arg_annotations() -> None:
    display_pesquisar = '@Command("*")\npublic void pesquisar()'
    display_automapping = '@WireVariable("/frmtestepessoa.zul")\npublic String automapping()'
    assert (
        canonicalize_java(
            qualified_name="FrmTestePessoa.pesquisar",
            display_signature=display_pesquisar,
        )
        == "FrmTestePessoa.pesquisar():void"
    )
    assert (
        canonicalize_java(
            qualified_name="FrmTestePessoa.automapping",
            display_signature=display_automapping,
        )
        == "FrmTestePessoa.automapping():String"
    )


def test_strip_java_annotations_removes_nested_annotation_parens() -> None:
    text = '@NotifyChange({"a", "b"}) public void method(String value)'
    stripped = _strip_java_annotations(text)
    assert "@" not in stripped
    assert "public void method(String value)" in stripped
