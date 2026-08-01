"""Generic empty-result hints for Grep and Glob (no domain heuristics)."""

from __future__ import annotations

import re

from code_harness.tools.search_globs import GlobInput, format_glob_label

_MAX_HINTS = 4

# Characters that often mean the pattern is a regex, not a literal name.
_REGEX_META = re.compile(r"[.\\^$*+?{}\[\]|()]")


def append_grep_hints(
    message: str,
    *,
    pattern: str,
    path: str | None,
    glob: GlobInput | None,
    file_type: str | None,
    case_insensitive: bool,
    include_all: bool,
    source_first_omitted: bool = False,
) -> str:
    hints: list[str] = []
    if not case_insensitive:
        hints.append("Try case_insensitive=true if letter case may differ.")
    if _looks_like_symbol_name(pattern):
        hints.append(f'Try output_mode="symbols" to find definitions named like "{pattern}".')
        hints.append(f'Try glob="*{pattern}*" to limit Grep to matching filenames.')
    elif _REGEX_META.search(pattern):
        hints.append("Simplify or escape the regex if you meant a literal string.")
    if glob is not None or file_type is not None:
        hints.append("Widen or remove glob/type filters if the match may be elsewhere.")
    if path not in (None, ".", ""):
        hints.append("Search from the project root (omit path) if the scope is too narrow.")
    if not include_all and not source_first_omitted:
        hints.append("Pass include_all=true to also search ignored harness paths.")
    return _with_hints(message, hints)


def append_glob_hints(
    message: str,
    *,
    glob_pattern: GlobInput,
    target_directory: str | None,
    include_all: bool,
    source_first_omitted: bool = False,
) -> str:
    label = format_glob_label(glob_pattern)
    hints: list[str] = []
    if isinstance(glob_pattern, str) and "*" not in glob_pattern and "?" not in glob_pattern:
        hints.append(f'Try a broader pattern such as "*{glob_pattern}*" or "*.ext".')
    elif "{" not in label:
        hints.append('Try brace expansion such as "*.{py,md,ts}" or pass a list of patterns.')
    if target_directory not in (None, ".", ""):
        hints.append("Omit target_directory to search the whole project.")
    if not include_all and not source_first_omitted:
        hints.append("Pass include_all=true to also list ignored harness paths.")
    if not hints:
        hints.append("Check the pattern spelling or try a simpler glob.")
    return _with_hints(message, hints)


def append_symbol_hints(
    message: str,
    *,
    pattern: str,
    path: str | None,
    include_all: bool,
    case_insensitive: bool,
    outlining_file: bool,
) -> str:
    hints: list[str] = []
    if not outlining_file:
        hints.append("Pass path to a source file for a full outline of that file.")
    if pattern.strip() and not case_insensitive:
        hints.append("Try case_insensitive=true if letter case may differ.")
    if pattern.strip():
        hints.append("Try a shorter substring of the symbol name.")
    if not include_all:
        hints.append("Pass include_all=true to also scan ignored harness paths.")
    if outlining_file and pattern.strip():
        hints.append("Omit pattern to list every symbol in the file.")
    return _with_hints(message, hints)


def _looks_like_symbol_name(pattern: str) -> bool:
    text = pattern.strip()
    if not text or _REGEX_META.search(text):
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text))


def _with_hints(message: str, hints: list[str]) -> str:
    selected = hints[:_MAX_HINTS]
    if not selected:
        return message
    lines = "\n".join(f"- {hint}" for hint in selected)
    return f"{message}\n\nSuggestions:\n{lines}"
