from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_harness.application.dto.requests import GetRepositoryMapRequest
from code_harness.application.tools._timing import timed
from code_harness.application.tools.index_state import resolve_index_state
from code_harness.domain.enums import IndexState
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.project import Project
from code_harness.domain.models.repository_map import (
    RepositoryDirectory,
    RepositoryFile,
    RepositoryMap,
    RepositorySymbol,
)
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.file_catalog import FileCatalog
from code_harness.domain.protocols.index_source_reader import IndexSourceReader
from code_harness.domain.protocols.repository_store import RepositoryStore

_KIND_PRIORITY = {
    "module": 0,
    "package": 0,
    "class": 1,
    "interface": 1,
    "enum": 1,
    "record": 1,
    "constructor": 2,
    "procedure": 3,
    "function": 3,
    "method": 4,
}


@dataclass(slots=True)
class _MutableDirectory:
    name: str
    path: str
    directories: dict[str, "_MutableDirectory"] = field(default_factory=dict)
    files: list[RepositoryFile] = field(default_factory=list)


def _freeze(directory: _MutableDirectory) -> RepositoryDirectory:
    return RepositoryDirectory(
        directory.name,
        directory.path,
        tuple(_freeze(directory.directories[name]) for name in sorted(directory.directories)),
        tuple(sorted(directory.files, key=lambda item: item.name.casefold())),
    )


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


class GetRepositoryMapTool:
    def __init__(
        self,
        project: Project,
        catalog: FileCatalog,
        store: RepositoryStore,
        reader: IndexSourceReader,
    ) -> None:
        self._project = project
        self._catalog = catalog
        self._store = store
        self._reader = reader

    def execute(self, request: GetRepositoryMapRequest) -> ToolResult[RepositoryMap]:
        def build() -> tuple[RepositoryMap, tuple[ToolWarning, ...]]:
            discovered = self._catalog.list_files(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
            )
            languages = {language.casefold() for language in request.languages}
            filtered = tuple(
                source
                for source in discovered
                if (
                    (not languages or (source.language or "").casefold() in languages)
                    and (
                        not request.path
                        or source.path == request.path
                        or source.path.startswith(f"{request.path.rstrip('/')}/")
                    )
                )
            )
            if request.max_depth is not None:
                depth_limited: list[SourceFile] = []
                for source in filtered:
                    depth = len(PurePosixPath(source.path).parts) - 1
                    if depth <= request.max_depth:
                        depth_limited.append(source)
                filtered = tuple(depth_limited)
            selected = filtered[: request.max_files]
            warnings: list[ToolWarning] = []
            index_state = resolve_index_state(self._store, self._project)
            symbol_results: tuple[StructuralSearchResult, ...] = ()
            include_symbols = request.effective_include_symbols
            try:
                status = self._store.get_status(self._project)
                if include_symbols and (
                    status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
                    and status.structural_schema_ready
                ):
                    collected: list[StructuralSearchResult] = []
                    selected_paths = tuple(_normalize_path(source.path) for source in selected)
                    for offset in range(0, len(selected_paths), 400):
                        batch = selected_paths[offset : offset + 400]
                        collected.extend(
                            self._store.list_symbols(
                                self._project.project_id,
                                batch,
                                limit=max(
                                    1,
                                    len(batch) * request.max_symbols_per_file * 4,
                                ),
                            )
                        )
                    symbol_results = tuple(collected)
                elif include_symbols:
                    warnings.append(
                        ToolWarning(
                            code="structural_index_not_ready",
                            message=(
                                "Structural index is not ready; repository map contains files only."
                            ),
                            recoverable=True,
                            capability="structural",
                            remediation="Run index_project before requesting repository symbols.",
                        )
                    )
            except CodeHarnessError as error:
                warnings.append(
                    ToolWarning(
                        code=error.code.value,
                        message=(
                            f"Structural repository map is unavailable ({error.code.value}); "
                            "returned files only."
                        ),
                        recoverable=True,
                        capability="structural",
                        remediation=error.remediation,
                    )
                )

            symbols: dict[str, tuple[RepositorySymbol, ...]] = {}
            if include_symbols:
                symbols, validation_warnings = self._validated_symbols(
                    symbol_results,
                    request.max_symbols_per_file,
                )
                warnings.extend(validation_warnings)
            if request.mode == "summary" and not include_symbols:
                tree = self._tree(selected, {})
            elif not request.include_files:
                tree = self._tree((), symbols)
            else:
                tree = self._tree(selected, symbols)
            unique_warnings = tuple(dict.fromkeys(warnings))
            return (
                RepositoryMap(
                    tree,
                    len(filtered),
                    len(selected),
                    max(0, len(filtered) - len(selected)),
                    index_state,
                    tuple(warning.message for warning in unique_warnings),
                ),
                unique_warnings,
            )

        (repository_map, warnings), elapsed_ms = timed(build)
        return ToolResult(
            repository_map,
            elapsed_ms,
            truncated=repository_map.omitted_files > 0,
            warnings=warnings,
            index_state=repository_map.index_state,
        )

    def _validated_symbols(
        self,
        results: tuple[StructuralSearchResult, ...],
        max_per_file: int,
    ) -> tuple[dict[str, tuple[RepositorySymbol, ...]], list[ToolWarning]]:
        grouped: dict[str, list[StructuralSearchResult]] = {}
        for result in results:
            if result.symbol is not None:
                path = _normalize_path(result.symbol.location.path)
                grouped.setdefault(path, []).append(result)

        mapped: dict[str, tuple[RepositorySymbol, ...]] = {}
        warnings: list[ToolWarning] = []
        for path, values in grouped.items():
            try:
                source = self._reader.load(path)
            except CodeHarnessError as error:
                warnings.append(
                    ToolWarning(
                        code=error.code.value,
                        message=(
                            f"Skipped symbols for {path}: current file is unavailable "
                            f"({error.code.value})."
                        ),
                        recoverable=True,
                        capability="filesystem",
                        remediation=error.remediation,
                    )
                )
                continue
            if any(value.file_hash != source.content_hash for value in values):
                warnings.append(
                    ToolWarning(
                        code="stale_structural_result",
                        message=f"Skipped stale repository-map symbols for {path}; reindex it.",
                        recoverable=True,
                        capability="structural",
                        remediation="Run index_project to refresh the structural index.",
                    )
                )
                continue
            values.sort(
                key=lambda value: (
                    _KIND_PRIORITY.get(value.symbol.kind if value.symbol else "", 9),
                    value.symbol.location.start_line if value.symbol else 0,
                    value.symbol.name if value.symbol else "",
                )
            )
            mapped[path] = tuple(
                RepositorySymbol(
                    symbol.name,
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.location.start_line,
                    symbol.location.end_line,
                )
                for result in values[:max_per_file]
                if (symbol := result.symbol) is not None
            )
        return mapped, warnings

    @staticmethod
    def _tree(
        files: tuple[SourceFile, ...],
        symbols: dict[str, tuple[RepositorySymbol, ...]],
    ) -> RepositoryDirectory:
        root = _MutableDirectory(".", "")
        normalized_symbols = {_normalize_path(path): value for path, value in symbols.items()}
        for source in files:
            parts = PurePosixPath(_normalize_path(source.path)).parts
            current = root
            for index, name in enumerate(parts[:-1], start=1):
                path = "/".join(parts[:index])
                current = current.directories.setdefault(name, _MutableDirectory(name, path))
            file_path = _normalize_path(source.path)
            current.files.append(
                RepositoryFile(
                    parts[-1],
                    file_path,
                    source.language,
                    source.size_bytes,
                    normalized_symbols.get(file_path, ()),
                )
            )
        return _freeze(root)
