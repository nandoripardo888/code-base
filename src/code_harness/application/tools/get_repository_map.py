from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_harness.application.dto.requests import GetRepositoryMapRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import IndexState
from code_harness.domain.errors import CodeHarnessError
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
    "procedure": 2,
    "function": 2,
    "method": 3,
    "constructor": 3,
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
        def build() -> RepositoryMap:
            discovered = self._catalog.list_files(
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
            )
            languages = {language.casefold() for language in request.languages}
            filtered = tuple(
                source
                for source in discovered
                if not languages or (source.language or "").casefold() in languages
            )
            selected = filtered[: request.max_files]
            warnings: list[str] = []
            index_state: str | None = None
            symbol_results: tuple[StructuralSearchResult, ...] = ()
            try:
                status = self._store.get_status(self._project)
                index_state = status.state.value
                if (
                    status.state in (IndexState.READY, IndexState.READY_WITH_WARNINGS)
                    and status.structural_schema_ready
                ):
                    collected: list[StructuralSearchResult] = []
                    selected_paths = tuple(source.path for source in selected)
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
                else:
                    warnings.append(
                        "Structural index is not ready; repository map contains files only."
                    )
            except CodeHarnessError as error:
                warnings.append(
                    f"Structural repository map is unavailable ({error.code.value}); "
                    "returned files only."
                )

            symbols, validation_warnings = self._validated_symbols(
                symbol_results,
                request.max_symbols_per_file,
            )
            warnings.extend(validation_warnings)
            tree = self._tree(selected, symbols)
            unique_warnings = tuple(dict.fromkeys(warnings))
            return RepositoryMap(
                tree,
                len(filtered),
                len(selected),
                max(0, len(filtered) - len(selected)),
                index_state,
                unique_warnings,
            )

        repository_map, elapsed_ms = timed(build)
        return ToolResult(
            repository_map,
            elapsed_ms,
            truncated=repository_map.omitted_files > 0,
            warnings=repository_map.warnings,
            index_state=repository_map.index_state,
        )

    def _validated_symbols(
        self,
        results: tuple[StructuralSearchResult, ...],
        max_per_file: int,
    ) -> tuple[dict[str, tuple[RepositorySymbol, ...]], list[str]]:
        grouped: dict[str, list[StructuralSearchResult]] = {}
        for result in results:
            if result.symbol is not None:
                grouped.setdefault(result.symbol.location.path, []).append(result)

        mapped: dict[str, tuple[RepositorySymbol, ...]] = {}
        warnings: list[str] = []
        for path, values in grouped.items():
            try:
                source = self._reader.load(path)
            except CodeHarnessError as error:
                warnings.append(
                    f"Skipped symbols for {path}: current file is unavailable ({error.code.value})."
                )
                continue
            if any(value.file_hash != source.content_hash for value in values):
                warnings.append(f"Skipped stale repository-map symbols for {path}; reindex it.")
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
        for source in files:
            parts = PurePosixPath(source.path).parts
            current = root
            for index, name in enumerate(parts[:-1], start=1):
                path = "/".join(parts[:index])
                current = current.directories.setdefault(name, _MutableDirectory(name, path))
            current.files.append(
                RepositoryFile(
                    parts[-1],
                    source.path,
                    source.language,
                    source.size_bytes,
                    symbols.get(source.path, ()),
                )
            )
        return _freeze(root)
