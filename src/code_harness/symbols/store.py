"""Symbol query facade - OnDemand now; IndexedSymbolStore can replace later."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from code_harness import ripgrep
from code_harness.encoding import decode_file
from code_harness.paths import PathGuard
from code_harness.symbols.extractors import ExtractorRegistry
from code_harness.symbols.languages import build_default_registry
from code_harness.symbols.models import Symbol
from code_harness.tools.search_globs import GlobInput, normalize_glob_patterns, with_recursive_prefix
from code_harness.tools.search_ignores import ignore_glob_flags

MATCH_CAP = 1_000
# Cap how many files we open during a project-wide find.
_FILE_SCAN_CAP = 2_000


class SymbolStore(Protocol):
    def outline(self, path: str, *, name_filter: str | None = None) -> list[Symbol]: ...

    def find(
        self,
        name: str,
        *,
        path: str | None = None,
        glob: GlobInput | None = None,
        file_type: str | None = None,
        case_insensitive: bool = False,
        include_all: bool = False,
        head_limit: int | None = None,
        offset: int = 0,
    ) -> list[Symbol]: ...


class OnDemandSymbolStore:
    def __init__(
        self,
        guard: PathGuard,
        *,
        registry: ExtractorRegistry | None = None,
    ) -> None:
        self._guard = guard
        self._registry = registry or build_default_registry()

    def outline(self, path: str, *, name_filter: str | None = None) -> list[Symbol]:
        absolute = self._guard.resolve(path, kind="file")
        relative = self._guard.relative(absolute)
        symbols = self._extract_file(absolute, relative)
        if name_filter:
            symbols = [
                symbol
                for symbol in symbols
                if _name_matches(symbol.name, name_filter, case_insensitive=False)
            ]
        return symbols

    def find(
        self,
        name: str,
        *,
        path: str | None = None,
        glob: GlobInput | None = None,
        file_type: str | None = None,
        case_insensitive: bool = False,
        include_all: bool = False,
        head_limit: int | None = None,
        offset: int = 0,
    ) -> list[Symbol]:
        limit = min(head_limit or MATCH_CAP, MATCH_CAP)
        skip = max(offset, 0)
        matches: list[Symbol] = []
        matched = 0

        for absolute, relative in self._candidate_files(
            path=path,
            glob=glob,
            file_type=file_type,
            include_all=include_all,
        ):
            for symbol in self._extract_file(absolute, relative):
                if not _name_matches(symbol.name, name, case_insensitive=case_insensitive):
                    continue
                index = matched
                matched += 1
                if index < skip:
                    continue
                if len(matches) >= limit:
                    return matches
                matches.append(symbol)
        return matches

    def _extract_file(self, absolute: Path, relative: str) -> list[Symbol]:
        try:
            source = decode_file(absolute).text
        except OSError:
            return []
        return self._registry.extract(_normalize_path(relative), source)

    def _candidate_files(
        self,
        *,
        path: str | None,
        glob: GlobInput | None,
        file_type: str | None,
        include_all: bool,
    ) -> list[tuple[Path, str]]:
        target = self._guard.resolve(path or ".", kind="any")
        if target.is_file():
            return [(target, self._guard.relative(target))]

        relative_dir = self._guard.relative(target)
        arguments = [
            "--hidden",
            "--glob",
            "!.git/",
            *ignore_glob_flags(self._guard.root, include_all=include_all),
            "--files",
        ]
        if glob is not None:
            for pattern in normalize_glob_patterns(glob):
                arguments.extend(["--glob", with_recursive_prefix(pattern)])
        if file_type:
            arguments.extend(["--type", file_type])
        arguments.extend(["--", relative_dir if relative_dir != "." else "."])
        output = ripgrep.run(arguments, cwd=self._guard.root)

        results: list[tuple[Path, str]] = []
        for line in output.splitlines():
            entry = line.strip()
            if not entry:
                continue
            relative = _normalize_path(entry)
            absolute = self._guard.root / relative
            if not absolute.is_file():
                continue
            results.append((absolute, relative))
            if len(results) >= _FILE_SCAN_CAP:
                break
        return results


def _normalize_path(text: str) -> str:
    return text.replace("\\", "/").removeprefix("./")


def _name_matches(symbol_name: str, needle: str, *, case_insensitive: bool) -> bool:
    if case_insensitive:
        return needle.casefold() in symbol_name.casefold()
    return needle in symbol_name
