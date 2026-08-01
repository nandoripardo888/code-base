"""Brace expansion and multi-pattern normalization for Grep/Glob filters."""

from __future__ import annotations

from code_harness.errors import InvalidArgumentError

GlobInput = str | list[str]


def normalize_glob_patterns(value: GlobInput) -> list[str]:
    """Expand braces and flatten a string or list of globs (order preserved, deduped)."""
    raw_items = [value] if isinstance(value, str) else list(value)
    if not raw_items:
        raise InvalidArgumentError("glob pattern list must not be empty.")

    expanded: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            raise InvalidArgumentError(
                f"glob patterns must be strings; got {type(item).__name__}."
            )
        text = item.strip()
        if not text:
            raise InvalidArgumentError("glob pattern must not be empty.")
        for pattern in expand_braces(text):
            if pattern not in seen:
                seen.add(pattern)
                expanded.append(pattern)
    return expanded


def with_recursive_prefix(pattern: str) -> str:
    return pattern if pattern.startswith("**/") else f"**/{pattern}"


def expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` / ``*.{py,md}`` bash-style; nested braces supported.

    Raises ``InvalidArgumentError`` on unmatched or empty brace groups.
    Patterns without braces are returned unchanged as a single-element list.
    """
    if "{" not in pattern and "}" not in pattern:
        return [pattern]
    if pattern.count("{") != pattern.count("}"):
        raise InvalidArgumentError(
            f"malformed brace pattern {pattern!r}: unmatched '{{' or '}}'."
        )

    start = pattern.find("{")
    if start < 0:
        if "}" in pattern:
            raise InvalidArgumentError(
                f"malformed brace pattern {pattern!r}: unmatched '}}'."
            )
        return [pattern]

    depth = 0
    end = -1
    for index in range(start, len(pattern)):
        char = pattern[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
            if depth < 0:
                raise InvalidArgumentError(
                    f"malformed brace pattern {pattern!r}: unmatched '}}'."
                )
    if end < 0:
        raise InvalidArgumentError(
            f"malformed brace pattern {pattern!r}: unmatched '{{'."
        )

    prefix = pattern[:start]
    body = pattern[start + 1 : end]
    suffix = pattern[end + 1 :]
    alternatives = _split_top_level(body)
    if not alternatives or any(part == "" for part in alternatives):
        raise InvalidArgumentError(
            f"malformed brace pattern {pattern!r}: empty alternative in braces."
        )

    results: list[str] = []
    seen: set[str] = set()
    for alternative in alternatives:
        for expanded_alt in expand_braces(alternative):
            for expanded_suffix in expand_braces(suffix):
                combined = f"{prefix}{expanded_alt}{expanded_suffix}"
                if combined not in seen:
                    seen.add(combined)
                    results.append(combined)
    return results


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise InvalidArgumentError(
                    f"malformed brace pattern: unmatched '}}' in {body!r}."
                )
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return parts


def format_glob_label(value: GlobInput) -> str:
    if isinstance(value, str):
        return value
    return ", ".join(value)
