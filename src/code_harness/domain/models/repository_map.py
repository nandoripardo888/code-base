from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepositorySymbol:
    name: str
    qualified_name: str | None
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    name: str
    path: str
    language: str | None
    size_bytes: int
    symbols: tuple[RepositorySymbol, ...] = ()


@dataclass(frozen=True, slots=True)
class RepositoryDirectory:
    name: str
    path: str
    directories: tuple["RepositoryDirectory", ...] = ()
    files: tuple[RepositoryFile, ...] = ()


@dataclass(frozen=True, slots=True)
class RepositoryMap:
    root: RepositoryDirectory
    total_files: int
    included_files: int
    omitted_files: int
    index_state: str | None
    warnings: tuple[str, ...] = ()
