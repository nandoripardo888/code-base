import shutil
import subprocess
from pathlib import Path

import pytest

from code_harness.application.dto.review_requests import (
    GetChangedSymbolsRequest,
    GetChangeSetRequest,
    ListChangedFilesRequest,
    ReadDiffRequest,
)
from code_harness.application.review import (
    GetChangedSymbolsTool,
    GetChangeSetTool,
    ListChangedFilesTool,
    ReadDiffTool,
)
from code_harness.domain.enums import ChangeSourceKind, FileChangeKind
from code_harness.domain.errors import ChangeSetNotFoundError
from code_harness.domain.models.change_set import ChangeSet, ChangeSetRequest
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.project import Project
from code_harness.domain.models.structural import CodeSymbol, StructuralSearchResult
from code_harness.infrastructure.git import LocalGitChangeProvider

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable"),
]


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(root), *args),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "init")
    return root


def test_local_git_change_provider_captures_working_tree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (root / "new.txt").write_text("hello\n", encoding="utf-8")
    provider = LocalGitChangeProvider(root, repository_id="repo-1")

    change_set = provider.create_change_set(
        ChangeSetRequest(
            source=ChangeSourceKind.WORKING_TREE,
            base="HEAD",
            include_untracked=True,
        )
    )
    diff = provider.read_diff(change_set)

    assert change_set.source_kind is ChangeSourceKind.WORKING_TREE
    assert {item.path for item in change_set.files} >= {"app.py", "new.txt"}
    app = next(item for item in diff.files if item.path == "app.py")
    assert app.kind is FileChangeKind.MODIFIED
    assert app.hunks
    assert (
        provider.get_change_set(change_set.change_set_id).change_set_id
        == change_set.change_set_id
    )


def test_review_tools_round_trip(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text("def main():\n    return 3\n", encoding="utf-8")
    provider = LocalGitChangeProvider(root, repository_id="repo-1")
    get_change_set = GetChangeSetTool(provider)
    list_files = ListChangedFilesTool(provider)
    read_diff = ReadDiffTool(provider)

    captured = get_change_set.execute(GetChangeSetRequest()).data
    files = list_files.execute(ListChangedFilesRequest(captured.change_set_id)).data
    diff = read_diff.execute(ReadDiffRequest(captured.change_set_id, path="app.py")).data

    assert isinstance(captured, ChangeSet)
    assert any(item.path == "app.py" for item in files)
    assert diff.files[0].path == "app.py"
    with pytest.raises(ChangeSetNotFoundError):
        provider.get_change_set("missing")


def test_get_changed_symbols_maps_hunk_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text(
        "def main():\n    value = 1\n    return value\n",
        encoding="utf-8",
    )
    provider = LocalGitChangeProvider(root, repository_id="repo-1")
    change_set = GetChangeSetTool(provider).execute(GetChangeSetRequest()).data

    class FakeStore:
        def get_outline(self, _project_id: str, path: str) -> tuple[StructuralSearchResult, ...]:
            symbol = CodeSymbol(
                symbol_id="sym-1",
                name="main",
                qualified_name="main",
                kind="function",
                location=CodeLocation(path=path, start_line=1, end_line=3),
            )
            return (
                StructuralSearchResult(
                    symbol=symbol,
                    reference=None,
                    content=None,
                    file_hash="h",
                ),
            )

    monkeypatch.setattr(
        "code_harness.application.review.get_changed_symbols.resolve_index_state",
        lambda *_args, **_kwargs: "ready",
    )
    tool = GetChangedSymbolsTool(
        project=Project("repo-1", str(root)),
        store=FakeStore(),  # type: ignore[arg-type]
        provider=provider,
    )
    result = tool.execute(GetChangedSymbolsRequest(change_set.change_set_id))

    assert result.data
    assert result.data[0].name == "main"
    assert result.data[0].hunk_ids
