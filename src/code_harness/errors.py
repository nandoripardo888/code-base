"""Typed errors raised by the tools.

Every error carries a short ``code`` so interfaces can render a stable prefix
without matching on message text.
"""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for every recoverable tool failure."""

    code = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def render(self) -> str:
        return f"{self.code}: {self.message}"


class InvalidArgumentError(HarnessError):
    code = "invalid_argument"


class ProjectNotFoundError(HarnessError):
    code = "project_not_found"

    def __init__(self, root: str) -> None:
        super().__init__(f"Project root is not an existing directory: {root}")


class PathOutsideProjectError(HarnessError):
    code = "path_outside_project"

    def __init__(self, path: str) -> None:
        super().__init__(f"Path resolves outside the project root: {path!r}")


class PathNotFoundError(HarnessError):
    code = "path_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(f"Path does not exist: {path}")


class InvalidPathKindError(HarnessError):
    code = "invalid_path_kind"

    def __init__(self, path: str, *, expected: str, actual: str) -> None:
        super().__init__(f"Expected a {expected} at {path}, found {actual}.")


class RipgrepUnavailableError(HarnessError):
    code = "ripgrep_unavailable"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} Install ripgrep or set CODE_HARNESS_RG to the full path of the executable."
        )


class ParserSupportUnavailableError(HarnessError):
    code = "parser_support_unavailable"

    def __init__(self, detail: str | None = None) -> None:
        prefix = f"{detail.rstrip()} " if detail else ""
        super().__init__(
            f"{prefix}Install structural parsers with: pip install 'code-harness[parsers]'."
        )


class UnsupportedReferenceLanguageError(HarnessError):
    code = "unsupported_reference_language"

    def __init__(self, path: str) -> None:
        super().__init__(
            f"References are not supported for {path}. Supported extensions: "
            ".py, .java, .js, .jsx, .ts, .tsx."
        )


class StringNotFoundError(HarnessError):
    code = "string_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(f"old_string was not found in {path}.")


class AmbiguousReplacementError(HarnessError):
    code = "ambiguous_replacement"

    def __init__(self, path: str, occurrences: int) -> None:
        super().__init__(
            f"old_string appears {occurrences} times in {path}. "
            "Add surrounding context to make it unique, or pass replace_all=true."
        )


class ExecutionError(HarnessError):
    code = "execution_failed"


class ShellUnavailableError(HarnessError):
    code = "shell_unavailable"

    def __init__(self, shell: str) -> None:
        super().__init__(f"Requested shell {shell!r} was not found.")


class ShellSyntaxMismatchError(HarnessError):
    code = "shell_syntax_mismatch"


class GitUnavailableError(HarnessError):
    code = "git_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "Git is required to apply patches. Install Git or add its executable to PATH."
        )


class PatchInvalidError(HarnessError):
    code = "invalid_patch"


class PatchApplyError(HarnessError):
    code = "patch_apply_failed"


class PatchConflictError(HarnessError):
    code = "patch_conflict"

    def __init__(self, paths: list[str]) -> None:
        rendered = ", ".join(paths)
        super().__init__(f"Files changed after the patch was prepared: {rendered}")


class PatchHistoryError(HarnessError):
    code = "patch_history_error"


class UnexpectedOccurrencesError(HarnessError):
    code = "unexpected_occurrences"

    def __init__(self, path: str, expected: int, actual: int) -> None:
        super().__init__(f"Expected {expected} occurrence(s) in {path}, but found {actual}.")


class StaleFileError(HarnessError):
    code = "stale_file"

    def __init__(self, path: str) -> None:
        super().__init__(f"The file changed after it was read: {path}")


class PatchRollbackConflictError(HarnessError):
    code = "rollback_conflict"

    def __init__(self, transaction_id: str, paths: list[str]) -> None:
        self.transaction_id = transaction_id
        self.paths = tuple(paths)
        rendered = ", ".join(paths)
        super().__init__(
            f"Transaction {transaction_id} cannot be rolled back because these files changed: "
            f"{rendered}"
        )
