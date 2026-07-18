from code_harness.infrastructure.filesystem.ignore_rules import DEFAULT_RG_EXCLUDES


class RipgrepCommandBuilder:
    def __init__(self, executable: str = "rg", *, max_file_size_bytes: int = 2_000_000) -> None:
        self._executable = executable
        self._max_file_size_bytes = max_file_size_bytes

    def build(
        self,
        *,
        query: str,
        regex: bool,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        case_sensitive: bool,
        max_results: int,
    ) -> list[str]:
        command = [
            self._executable,
            "--json",
            "--line-number",
            "--column",
            "--color=never",
            "--hidden",
            "--max-filesize",
            str(self._max_file_size_bytes),
            "--max-count",
            str(max_results),
            "--case-sensitive" if case_sensitive else "--ignore-case",
        ]
        if not regex:
            command.append("--fixed-strings")
        for pattern in DEFAULT_RG_EXCLUDES:
            command.extend(("--glob", f"!{pattern}"))
        for pattern in include_globs:
            command.extend(("--glob", pattern))
        for pattern in exclude_globs:
            command.extend(("--glob", pattern if pattern.startswith("!") else f"!{pattern}"))
        command.extend(("--", query, "."))
        return command
