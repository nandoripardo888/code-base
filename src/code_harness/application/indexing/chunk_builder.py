from dataclasses import replace
from hashlib import sha256

from code_harness.domain.enums import ParseState
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.index_report import IndexedSource
from code_harness.domain.models.structural import AnalyzeResult, CodeChunk

CHUNKING_VERSION = "1"


def build_chunks(
    source: IndexedSource,
    analysis: AnalyzeResult,
    *,
    target_chars: int = 4_000,
    max_chars: int = 8_000,
) -> AnalyzeResult:
    chunks: list[CodeChunk] = []
    for chunk in analysis.chunks:
        if len(chunk.content) <= max_chars:
            chunks.append(chunk)
        else:
            chunks.extend(
                _text_chunks(
                    source,
                    start_line=chunk.location.start_line,
                    end_line=chunk.location.end_line,
                    target_chars=target_chars,
                    max_chars=max_chars,
                    symbol_id=chunk.symbol_id,
                    parent_chunk_id=chunk.chunk_id,
                    kind="symbol_part",
                )
            )
    if not chunks:
        chunks.extend(
            _text_chunks(
                source,
                start_line=1,
                end_line=max(1, len(source.content.splitlines())),
                target_chars=target_chars,
                max_chars=max_chars,
                symbol_id=None,
                parent_chunk_id=None,
                kind="text",
            )
        )
    return replace(analysis, chunks=tuple(chunks))


def textual_fallback(source: IndexedSource, warning: str | None = None) -> AnalyzeResult:
    result = AnalyzeResult(
        parser_name="textual-fallback",
        parser_version=CHUNKING_VERSION,
        state=ParseState.FALLBACK if warning is None else ParseState.FAILED,
        warnings=(warning,) if warning else (),
    )
    return build_chunks(source, result)


def _text_chunks(
    source: IndexedSource,
    *,
    start_line: int,
    end_line: int,
    target_chars: int,
    max_chars: int,
    symbol_id: str | None,
    parent_chunk_id: str | None,
    kind: str,
) -> list[CodeChunk]:
    lines = source.content.splitlines(keepends=True)
    if not lines:
        lines = [""]
    end_line = min(max(start_line, end_line), len(lines))
    chunks: list[CodeChunk] = []
    cursor = start_line - 1
    stop = end_line
    while cursor < stop:
        selected: list[str] = []
        selected_chars = 0
        chunk_start = cursor + 1
        while cursor < stop:
            line = lines[cursor]
            if selected and selected_chars + len(line) > target_chars:
                break
            selected.append(line[:max_chars] if not selected else line)
            selected_chars += len(selected[-1])
            cursor += 1
            if selected_chars >= max_chars:
                break
        if not selected:
            selected.append(lines[cursor][:max_chars])
            cursor += 1
        content = "".join(selected)[:max_chars]
        content_hash = sha256(content.encode("utf-8")).hexdigest()
        chunk_end = chunk_start + len(selected) - 1
        chunk_id = sha256(
            f"{source.path}\x1f{symbol_id or f'{chunk_start}:{chunk_end}'}\x1f"
            f"{CHUNKING_VERSION}\x1f{content_hash}".encode()
        ).hexdigest()[:32]
        chunks.append(
            CodeChunk(
                chunk_id,
                CodeLocation(source.path, chunk_start, chunk_end),
                content,
                content_hash,
                kind,
                symbol_id,
                parent_chunk_id,
            )
        )
    return chunks
