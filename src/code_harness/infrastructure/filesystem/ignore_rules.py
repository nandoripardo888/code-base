from pathlib import Path

import pathspec

DEFAULT_IGNORES = (
    ".git/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
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
    "*.pdf",
    ".env",
    "*.pem",
    "*.key",
)

DEFAULT_RG_EXCLUDES = (
    ".git/**",
    ".idea/**",
    ".vscode/**",
    ".venv/**",
    "venv/**",
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
    "**/*.pdf",
    "**/.env",
    "**/*.pem",
    "**/*.key",
)


class IgnoreRules:
    def __init__(self, root: Path, *, use_gitignore: bool = True) -> None:
        self._safe = pathspec.PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORES)
        gitignore = root / ".gitignore"
        lines: list[str] = []
        if use_gitignore and gitignore.is_file():
            lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
        self._gitignore = pathspec.PathSpec.from_lines("gitwildmatch", lines)

    def is_ignored(self, relative_path: str, *, is_directory: bool = False) -> bool:
        normalized = relative_path.replace("\\", "/")
        candidate = f"{normalized}/" if is_directory else normalized
        return self._safe.match_file(candidate) or self._gitignore.match_file(candidate)


def compile_globs(patterns: tuple[str, ...]) -> pathspec.PathSpec | None:
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
