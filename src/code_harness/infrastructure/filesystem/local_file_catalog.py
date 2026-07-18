import os
from pathlib import Path

from code_harness.domain.errors import PathOutsideProjectError, SourceFileNotFoundError
from code_harness.domain.models.source_file import SourceFile
from code_harness.infrastructure.filesystem.ignore_rules import IgnoreRules, compile_globs
from code_harness.infrastructure.filesystem.language_detection import detect_language
from code_harness.infrastructure.filesystem.path_guard import PathGuard


class LocalFileCatalog:
    def __init__(self, guard: PathGuard, *, use_gitignore: bool = True) -> None:
        self._guard = guard
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
        for directory, dirnames, filenames in os.walk(self._guard.root, followlinks=False):
            directory_path = Path(directory)
            kept_directories: list[str] = []
            for dirname in dirnames:
                relative = (directory_path / dirname).relative_to(self._guard.root).as_posix()
                if not self._ignore.is_ignored(relative, is_directory=True):
                    kept_directories.append(dirname)
            dirnames[:] = kept_directories

            for filename in filenames:
                raw_path = directory_path / filename
                relative = raw_path.relative_to(self._guard.root).as_posix()
                if self._ignore.is_ignored(relative):
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
