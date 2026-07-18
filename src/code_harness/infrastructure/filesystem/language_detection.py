from pathlib import PurePath

LANGUAGES_BY_SUFFIX = {
    ".java": "java",
    ".py": "python",
    ".pyi": "python",
    ".pck": "plsql",
    ".pkb": "plsql",
    ".pks": "plsql",
    ".fnc": "plsql",
    ".prc": "plsql",
    ".trg": "plsql",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".txt": "text",
    ".properties": "properties",
    ".gradle": "gradle",
    ".groovy": "groovy",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
}


def detect_language(path: str) -> str | None:
    pure_path = PurePath(path)
    name = pure_path.name.casefold()
    if name in {"dockerfile", "makefile"}:
        return name
    return LANGUAGES_BY_SUFFIX.get(pure_path.suffix.casefold())
