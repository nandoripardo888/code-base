"""On-demand syntactic reference search backed by optional Tree-sitter parsers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from code_harness import ripgrep
from code_harness.encoding import decode_file
from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.symbols.models import Reference, ReferenceKind
from code_harness.symbols.parsers import SUPPORTED_EXTENSIONS, TreeSitterRegistry
from code_harness.tools.search_core import MATCH_CAP, base_rg_arguments, normalize_path
from code_harness.tools.search_globs import (
    GlobInput,
    exclusion_glob_flags,
    normalize_glob_patterns,
)

_FILE_SCAN_CAP = 2_000
_REFERENCE_SCAN_CAP = 10_000
_KIND_PRIORITY: dict[ReferenceKind, int] = {
    "definition": 0,
    "implementation": 1,
    "instantiation": 2,
    "call": 3,
    "type_use": 4,
    "import": 5,
    "usage": 6,
}
_REFERENCE_KINDS = frozenset(_KIND_PRIORITY)


def find_references(
    guard: PathGuard,
    *,
    pattern: str,
    path: str | None = None,
    glob: GlobInput | None = None,
    file_type: str | None = None,
    case_insensitive: bool = False,
    head_limit: int | None = None,
    offset: int = 0,
    include_all: bool = False,
    exclude: GlobInput | None = None,
    reference_kind: str | Sequence[str] | None = None,
    exclude_reference_kind: str | Sequence[str] | None = None,
    registry: TreeSitterRegistry | None = None,
) -> str:
    normalized_identifier = pattern.replace("$", "_")
    if not normalized_identifier.isidentifier():
        raise InvalidArgumentError(
            "output_mode=references requires one exact identifier, not a regex or phrase."
        )
    parser_registry = registry or TreeSitterRegistry()
    parser_registry.require_runtime()
    included_kinds = _normalize_reference_kinds(reference_kind, parameter="reference_kind")
    excluded_kinds = _normalize_reference_kinds(
        exclude_reference_kind,
        parameter="exclude_reference_kind",
    )
    candidates = _candidate_files(
        guard,
        pattern=pattern,
        path=path,
        glob=glob,
        file_type=file_type,
        case_insensitive=case_insensitive,
        include_all=include_all,
        exclude=exclude,
        registry=parser_registry,
    )

    references: list[Reference] = []
    partial_files = 0
    truncated_scan = False
    for absolute, relative in candidates:
        try:
            source = decode_file(absolute).text
        except OSError:
            continue
        parsed = parser_registry.parse(relative, source)
        partial_files += int(parsed.has_errors)
        for reference in parsed.references:
            if not _identifier_matches(
                reference.name,
                pattern,
                case_insensitive=case_insensitive,
            ):
                continue
            if included_kinds is not None and reference.kind not in included_kinds:
                continue
            if excluded_kinds is not None and reference.kind in excluded_kinds:
                continue
            references.append(reference)
            if len(references) >= _REFERENCE_SCAN_CAP:
                truncated_scan = True
                break
        if truncated_scan:
            break

    if not references:
        return (
            f"No syntactic references found for {pattern!r}.\n\n"
            "Suggestions:\n"
            '- Confirm the exact identifier name with output_mode="symbols".\n'
            "- Broaden path/glob/type/exclude filters if needed."
        )

    ordered = _order_grouped(references)
    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    window = ordered[offset : offset + limit]
    if not window:
        return f"No references in range (total {len(ordered)}, offset {offset})."

    rendered = _render_references(
        window,
        total=len(ordered),
        total_files=len({item.path for item in ordered}),
    )
    remaining = len(ordered) - (offset + len(window))
    if remaining > 0:
        rendered += f"\n\n({remaining} more references; pass offset={offset + len(window)})"
    if partial_files:
        rendered += (
            f"\n\n(Parsed {partial_files} file(s) with syntax errors; valid nodes were kept.)"
        )
    if truncated_scan:
        rendered += f"\n\n(Reference scan capped at {_REFERENCE_SCAN_CAP} occurrences.)"
    return rendered


def _normalize_reference_kinds(
    value: str | Sequence[str] | None,
    *,
    parameter: str,
) -> frozenset[ReferenceKind] | None:
    if value is None:
        return None
    raw_items = [value] if isinstance(value, str) else value
    items = {
        item.strip()
        for raw_item in raw_items
        for item in raw_item.split(",")
        if item.strip()
    }
    invalid = sorted(items - _REFERENCE_KINDS)
    if invalid:
        allowed = ", ".join(sorted(_REFERENCE_KINDS))
        raise InvalidArgumentError(
            f"{parameter} contains invalid reference kind(s): {', '.join(invalid)}. "
            f"Allowed values: {allowed}."
        )
    return frozenset(cast(ReferenceKind, item) for item in items)


def _candidate_files(
    guard: PathGuard,
    *,
    pattern: str,
    path: str | None,
    glob: GlobInput | None,
    file_type: str | None,
    case_insensitive: bool,
    include_all: bool,
    exclude: GlobInput | None,
    registry: TreeSitterRegistry,
) -> list[tuple[Path, str]]:
    target = guard.resolve(path or ".", kind="any")
    if target.is_file():
        relative = normalize_path(guard.relative(target))
        if not registry.supports(relative):
            from code_harness.errors import UnsupportedReferenceLanguageError

            raise UnsupportedReferenceLanguageError(relative)
        return [(target, relative)]

    relative_target = guard.relative(target)
    arguments = [
        *base_rg_arguments(guard.root, include_all=include_all, exclude=exclude),
        "--files-with-matches",
        "--fixed-strings",
    ]
    if case_insensitive:
        arguments.append("--ignore-case")
    if glob is not None:
        for item in normalize_glob_patterns(glob):
            arguments.extend(["--glob", item])
    if file_type:
        arguments.extend(["--type", file_type])
    arguments.extend(exclusion_glob_flags(exclude))
    arguments.extend(["--regexp", pattern, "--", relative_target])
    output = ripgrep.run(arguments, cwd=guard.root)

    candidates: list[tuple[Path, str]] = []
    for raw in output.splitlines():
        relative = normalize_path(raw.strip())
        if not relative or Path(relative).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        absolute = guard.root / relative
        if absolute.is_file():
            candidates.append((absolute, relative))
        if len(candidates) >= _FILE_SCAN_CAP:
            break
    candidates.sort(key=lambda item: item[1])
    return candidates


def _identifier_matches(name: str, pattern: str, *, case_insensitive: bool) -> bool:
    if case_insensitive:
        return name.casefold() == pattern.casefold()
    return name == pattern


def _order_grouped(references: list[Reference]) -> list[Reference]:
    deduplicated: dict[tuple[str, int, int, ReferenceKind], Reference] = {}
    for reference in references:
        key = (reference.path, reference.line, reference.column, reference.kind)
        deduplicated.setdefault(key, reference)

    grouped: dict[str, list[Reference]] = defaultdict(list)
    for reference in deduplicated.values():
        grouped[reference.path].append(reference)
    for items in grouped.values():
        items.sort(key=lambda item: (_KIND_PRIORITY[item.kind], item.line, item.column))
    paths = sorted(
        grouped,
        key=lambda value: (min(_KIND_PRIORITY[item.kind] for item in grouped[value]), value),
    )
    return [item for path in paths for item in grouped[path]]


def _render_references(
    references: list[Reference],
    *,
    total: int,
    total_files: int,
) -> str:
    occurrence_word = "occurrence" if total == 1 else "occurrences"
    file_word = "file" if total_files == 1 else "files"
    lines = [f"{total} syntactic {occurrence_word} in {total_files} {file_word}", ""]
    current_path: str | None = None
    for reference in references:
        if reference.path != current_path:
            if current_path is not None:
                lines.append("")
            lines.append(reference.path)
            current_path = reference.path
        metadata = str(reference.kind)
        if reference.container:
            metadata += f" in {reference.container}"
        lines.append(f"  {reference.line}:{reference.column} [{metadata}] {reference.excerpt}")
    return "\n".join(lines)
