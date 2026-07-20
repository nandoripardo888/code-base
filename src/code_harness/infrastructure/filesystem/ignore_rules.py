import os
from pathlib import Path

import pathspec

DEFAULT_IGNORES = (
    ".git/",
    ".code-harness/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
    ".tox/",
    ".nox/",
    ".cache/",
    ".uv-cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "__pycache__/",
    "node_modules/",
    "target/",
    "build/",
    "dist/",
    "coverage/",
    "logs/",
    "*.class",
    "*.jar",
    "*.war",
    "*.zip",
    "*.exe",
    "*.dll",
    "*.so",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.pdf",
    ".env",
    "*.pem",
    "*.key",
)

DEFAULT_RG_EXCLUDES = (
    ".git/**",
    ".code-harness/**",
    ".idea/**",
    ".vscode/**",
    ".venv/**",
    "venv/**",
    ".tox/**",
    ".nox/**",
    ".cache/**",
    ".uv-cache/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/target/**",
    "**/build/**",
    "**/dist/**",
    "**/coverage/**",
    "**/logs/**",
    "**/*.class",
    "**/*.jar",
    "**/*.war",
    "**/*.zip",
    "**/*.exe",
    "**/*.dll",
    "**/*.so",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.ico",
    "**/*.pdf",
    "**/.env",
    "**/*.pem",
    "**/*.key",
)


class IgnoreRules:
    def __init__(self, root: Path, *, use_gitignore: bool = True) -> None:
        self._safe = pathspec.PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORES)
        self._gitignores: list[tuple[str, pathspec.GitIgnoreSpec]] = []
        if use_gitignore:
            self._load_gitignores(root)

    def is_safely_ignored(self, relative_path: str, *, is_directory: bool = False) -> bool:
        normalized = relative_path.replace("\\", "/")
        candidate = f"{normalized}/" if is_directory else normalized
        return self._safe.match_file(candidate)

    def is_ignored(self, relative_path: str, *, is_directory: bool = False) -> bool:
        normalized = relative_path.replace("\\", "/")
        if self.is_safely_ignored(normalized, is_directory=is_directory):
            return True
        return self._matches_gitignore(normalized, is_directory=is_directory)

    def _load_gitignores(self, root: Path) -> None:
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            base = directory_path.relative_to(root).as_posix()
            if base == ".":
                base = ""
            if ".gitignore" in filenames:
                gitignore = directory_path / ".gitignore"
                lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
                self._gitignores.append((base, pathspec.GitIgnoreSpec.from_lines(lines)))
            kept: list[str] = []
            for dirname in dirnames:
                relative = (directory_path / dirname).relative_to(root).as_posix()
                if not self.is_ignored(relative, is_directory=True):
                    kept.append(dirname)
            dirnames[:] = kept

    def _matches_gitignore(self, normalized: str, *, is_directory: bool) -> bool:
        ignored = False
        for base, spec in self._gitignores:
            if base:
                prefix = f"{base}/"
                if not normalized.startswith(prefix):
                    continue
                local = normalized[len(prefix) :]
            else:
                local = normalized
            candidate = f"{local}/" if is_directory else local
            matched = spec.check_file(candidate).include
            if matched is not None:
                ignored = matched
        return ignored


def compile_globs(patterns: tuple[str, ...]) -> pathspec.PathSpec | None:
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
