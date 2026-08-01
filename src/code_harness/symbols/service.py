"""Orchestrate symbol queries and render Grep-facing text."""

from __future__ import annotations

from code_harness.errors import InvalidArgumentError
from code_harness.paths import PathGuard
from code_harness.symbols.models import Symbol
from code_harness.symbols.store import MATCH_CAP, OnDemandSymbolStore, SymbolStore
from code_harness.tools.search_globs import GlobInput
from code_harness.tools.search_hints import append_symbol_hints


def grep_symbols(
    guard: PathGuard,
    *,
    pattern: str,
    path: str | None = None,
    glob: GlobInput | None = None,
    file_type: str | None = None,
    case_insensitive: bool = False,
    head_limit: int | None = None,
    offset: int | None = None,
    include_all: bool = False,
    store: SymbolStore | None = None,
) -> str:
    symbol_store = store or OnDemandSymbolStore(guard)
    target = guard.resolve(path or ".", kind="any")
    name = pattern.strip()

    if target.is_file():
        name_filter = name or None
        symbols = symbol_store.outline(guard.relative(target), name_filter=name_filter)
        symbols = _page(symbols, head_limit=head_limit, offset=offset or 0)
    else:
        if not name:
            raise InvalidArgumentError(
                "output_mode=symbols requires a non-empty pattern when path is a "
                "directory or omitted; pass path to a file for outline."
            )
        symbols = symbol_store.find(
            name,
            path=path,
            glob=glob,
            file_type=file_type,
            case_insensitive=case_insensitive,
            include_all=include_all,
            head_limit=head_limit,
            offset=offset or 0,
        )

    if not symbols:
        return append_symbol_hints(
            "No symbols found.",
            pattern=pattern,
            path=path,
            include_all=include_all,
            case_insensitive=case_insensitive,
            outlining_file=target.is_file(),
        )
    return render_symbols(symbols)


def render_symbols(symbols: list[Symbol]) -> str:
    files = {symbol.path for symbol in symbols}
    lines: list[str] = []
    if len(files) > 1:
        count = len(symbols)
        symbol_word = "symbol" if count == 1 else "symbols"
        file_word = "file" if len(files) == 1 else "files"
        lines.append(f"{count} {symbol_word} in {len(files)} {file_word}")
        lines.append("")

    current_path: str | None = None
    for symbol in symbols:
        if symbol.path != current_path:
            if current_path is not None:
                lines.append("")
            lines.append(symbol.path)
            current_path = symbol.path
        lines.append(f"  {symbol.line} {_format_symbol(symbol)}")
    return "\n".join(lines)


def _format_symbol(symbol: Symbol) -> str:
    if symbol.container:
        return f"{symbol.kind} {symbol.container}.{symbol.name}"
    return f"{symbol.kind} {symbol.name}"


def _page(
    symbols: list[Symbol],
    *,
    head_limit: int | None,
    offset: int,
) -> list[Symbol]:
    limit = min(head_limit or MATCH_CAP, MATCH_CAP)
    return symbols[offset : offset + limit]
