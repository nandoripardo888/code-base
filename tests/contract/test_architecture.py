import ast
from pathlib import Path


def _project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name for alias in node.names if alias.name.startswith("code_harness")
            )
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("code_harness"):
            imports.add(node.module or "")
    return imports


def test_domain_does_not_import_outer_layers() -> None:
    root = Path("src/code_harness/domain")
    for source in root.rglob("*.py"):
        imports = _project_imports(source)
        assert all(name.startswith("code_harness.domain") for name in imports), source


def test_application_does_not_import_infrastructure_or_interfaces() -> None:
    root = Path("src/code_harness/application")
    forbidden = ("code_harness.infrastructure", "code_harness.interfaces", "code_harness.bootstrap")
    for source in root.rglob("*.py"):
        imports = _project_imports(source)
        assert not any(name.startswith(forbidden) for name in imports), source


def test_mcp_sdk_is_not_imported_outside_future_adapter() -> None:
    for source in Path("src/code_harness").rglob("*.py"):
        assert not any(
            name == "mcp" or name.startswith("mcp.") for name in _project_imports(source)
        )
