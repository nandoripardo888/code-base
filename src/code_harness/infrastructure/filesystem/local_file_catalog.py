import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from code_harness.domain.errors import PathOutsideProjectError, SourceFileNotFoundError
from code_harness.domain.models.source_file import SourceFile
from code_harness.infrastructure.filesystem.ignore_rules import IgnoreRules, compile_globs
from code_harness.infrastructure.filesystem.language_detection import detect_language
from code_harness.infrastructure.filesystem.path_guard import PathGuard


class LocalFileCatalog:
    def __init__(self, guard: PathGuard, *, use_gitignore: bool = True) -> None:
        self._guard = guard
        self._use_gitignore = use_gitignore
        self._ignore = IgnoreRules(guard.root, use_gitignore=use_gitignore)

    def list_files(
        self,
        *,
        include_globs: tuple[str, ...] = (),
        exclude_globs: tuple[str, ...] = (),
    ) -> tuple[SourceFile, ...]:
        include_spec = compile_globs(include_globs)
        exclude_spec = compile_globs(exclude_globs)
        files: list[SourceFile] = []
        for raw_path, git_filtered in self._candidate_paths():
            relative = raw_path.relative_to(self._guard.root).as_posix()
            ignored = (
                self._ignore.is_safely_ignored(relative)
                if git_filtered
                else self._ignore.is_ignored(relative)
            )
            if ignored:
                continue
            if include_spec is not None and not include_spec.match_file(relative):
                continue
            if exclude_spec is not None and exclude_spec.match_file(relative):
                continue
            try:
                resolved, relative = self._guard.resolve_file(relative)
                stat = resolved.stat()
            except (OSError, PathOutsideProjectError, SourceFileNotFoundError):
                continue
            files.append(
                SourceFile(
                    path=relative,
                    size_bytes=stat.st_size,
                    modified_at_ns=stat.st_mtime_ns,
                    language=detect_language(relative),
                )
            )
        files.sort(key=lambda item: item.path)
        return tuple(files)

    def _candidate_paths(self) -> Iterator[tuple[Path, bool]]:
        git_paths = self._git_paths() if self._use_gitignore else None
        if git_paths is not None:
            yield from ((self._guard.root / item, True) for item in git_paths)
            return
        yield from ((item, False) for item in self._walk_paths())

    def _git_paths(self) -> tuple[str, ...] | None:
        try:
            completed = subprocess.run(
                (
                    "git",
                    "-C",
                    str(self._guard.root),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    ".",
                ),
                check=False,
                capture_output=True,
                timeout=15.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        decoded = completed.stdout.decode("utf-8", errors="surrogateescape")
        return tuple(path for path in decoded.split("\0") if path)

    def _walk_paths(self) -> Iterator[Path]:
        for directory, dirnames, filenames in os.walk(self._guard.root, followlinks=False):
            directory_path = Path(directory)
            kept_directories: list[str] = []
            for dirname in dirnames:
                relative = (directory_path / dirname).relative_to(self._guard.root).as_posix()
                if not self._ignore.is_ignored(relative, is_directory=True):
                    kept_directories.append(dirname)
            dirnames[:] = kept_directories

            for filename in filenames:
                yield directory_path / filename
