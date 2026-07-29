from __future__ import annotations

from pathlib import Path

from code_harness.domain.enums import ChangeSegmentKind, WorkspaceTopologyKind
from code_harness.domain.models.workspace_topology import TopologySegment, WorkspaceTopology

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".code-harness",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "venv",
        "dist",
        "build",
    }
)


def _is_git_metadata(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        return (path / "HEAD").exists() or (path / "objects").exists()
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return False
        return text.startswith("gitdir:")
    return False


def _segment_id_for(relative_root: str) -> str:
    if not relative_root or relative_root in {".", "./"}:
        return "root"
    cleaned = relative_root.replace("\\", "/").strip("/")
    return cleaned.replace("/", "__") or "root"


class FilesystemTopologyResolver:
    """Discover Git repositories and loose (non-Git) roots under a workspace."""

    def resolve(self, workspace_root: str) -> WorkspaceTopology:
        root = Path(workspace_root).expanduser().resolve(strict=False)
        repositories = self._discover_repositories(root)
        loose_roots = self._discover_loose_roots(root, repositories)
        segments = self._build_segments(root, repositories, loose_roots)
        kind = self._classify(root, repositories, loose_roots)
        return WorkspaceTopology(
            workspace_root=str(root),
            kind=kind,
            git_repositories=tuple(str(path) for path in repositories),
            loose_roots=loose_roots,
            segments=segments,
        )

    def _discover_repositories(self, root: Path) -> tuple[Path, ...]:
        found: list[Path] = []
        root_git = root / ".git"
        if _is_git_metadata(root_git):
            found.append(root)
            self._walk_nested(root, found)
            return tuple(sorted(set(found), key=lambda item: str(item).casefold()))
        self._walk_nested(root, found)
        return tuple(sorted(set(found), key=lambda item: str(item).casefold()))

    def _walk_nested(self, root: Path, found: list[Path]) -> None:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir():
                    continue
                name = entry.name
                if name in _SKIP_DIR_NAMES:
                    continue
                git_meta = entry / ".git"
                if _is_git_metadata(git_meta):
                    found.append(entry)
                    continue
                stack.append(entry)

    def _discover_loose_roots(
        self,
        root: Path,
        repositories: tuple[Path, ...],
    ) -> tuple[str, ...]:
        if not repositories:
            return (".",)
        repo_set = {path.resolve(strict=False) for path in repositories}
        if root in repo_set and len(repositories) == 1:
            return ()
        loose: list[str] = []
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return ()
        for child in children:
            if child.name in _SKIP_DIR_NAMES:
                continue
            resolved = child.resolve(strict=False)
            if resolved in repo_set:
                continue
            if any(resolved == repo or repo in resolved.parents for repo in repo_set):
                continue
            relative = child.relative_to(root).as_posix()
            if relative not in loose:
                loose.append(relative)
        return tuple(sorted(loose, key=str.casefold))

    def _build_segments(
        self,
        root: Path,
        repositories: tuple[Path, ...],
        loose_roots: tuple[str, ...],
    ) -> tuple[TopologySegment, ...]:
        segments: list[TopologySegment] = []
        for repo in repositories:
            relative = "." if repo == root else repo.relative_to(root).as_posix()
            segments.append(
                TopologySegment(
                    segment_id=_segment_id_for(relative),
                    kind=ChangeSegmentKind.GIT_WORKTREE,
                    relative_root=relative,
                    source_root=str(repo),
                    repository_root=str(repo),
                )
            )
        if loose_roots:
            if not repositories:
                segments.append(
                    TopologySegment(
                        segment_id="root",
                        kind=ChangeSegmentKind.WORKSPACE_MIRROR,
                        relative_root=".",
                        source_root=str(root),
                        repository_root=None,
                    )
                )
            else:
                segments.append(
                    TopologySegment(
                        segment_id="loose-files",
                        kind=ChangeSegmentKind.WORKSPACE_MIRROR,
                        relative_root=".",
                        source_root=str(root),
                        repository_root=None,
                    )
                )
        return tuple(segments)

    def _classify(
        self,
        root: Path,
        repositories: tuple[Path, ...],
        loose_roots: tuple[str, ...],
    ) -> WorkspaceTopologyKind:
        if not repositories:
            return WorkspaceTopologyKind.NON_GIT
        if len(repositories) == 1 and repositories[0] == root and not loose_roots:
            return WorkspaceTopologyKind.SINGLE_GIT
        if len(repositories) > 1 or loose_roots:
            return WorkspaceTopologyKind.COMPOSITE
        if len(repositories) == 1 and repositories[0] != root:
            return WorkspaceTopologyKind.COMPOSITE
        return WorkspaceTopologyKind.SINGLE_GIT
