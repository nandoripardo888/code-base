from __future__ import annotations

from dataclasses import dataclass

from code_harness.application.context.token_budget import estimate_tokens
from code_harness.application.dto.review_requests import (
    BuildReviewContextRequest,
    FindChangeImpactsRequest,
    GetChangedSymbolsRequest,
    ReadDiffRequest,
)
from code_harness.application.review.find_change_impacts import FindChangeImpactsTool
from code_harness.application.review.get_changed_symbols import GetChangedSymbolsTool
from code_harness.application.review.read_diff import ReadDiffTool
from code_harness.application.tools._timing import timed
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.context import ContextSnippet
from code_harness.domain.models.review import ReviewContextBundle
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.index_source_reader import IndexSourceReader

_ROLE_PRIORITY = {
    "diff": 0,
    "changed_symbol": 1,
    "definition": 2,
    "caller": 3,
    "test": 4,
    "config": 5,
}


@dataclass(slots=True)
class _PendingSnippet:
    role: str
    path: str
    start_line: int
    end_line: int
    content: str
    language: str | None
    file_hash: str
    reason: str
    score: float = 1.0


class BuildReviewContextTool:
    def __init__(
        self,
        *,
        reader: IndexSourceReader,
        read_diff: ReadDiffTool,
        changed_symbols: GetChangedSymbolsTool,
        find_impacts: FindChangeImpactsTool,
    ) -> None:
        self._reader = reader
        self._read_diff = read_diff
        self._changed_symbols = changed_symbols
        self._find_impacts = find_impacts

    def execute(self, request: BuildReviewContextRequest) -> ToolResult[ReviewContextBundle]:
        warnings: list[str | ToolWarning] = []
        limitations: list[str] = []

        def resolve() -> ReviewContextBundle:
            pending: list[_PendingSnippet] = []
            coverage = {
                "diff_files": 0,
                "changed_symbols": 0,
                "callers": 0,
                "tests": 0,
                "configs": 0,
            }
            diff = self._read_diff.execute(
                ReadDiffRequest(change_set_id=request.change_set_id, path=request.path)
            )
            warnings.extend(diff.warnings)
            for changed_file in diff.data.files:
                coverage["diff_files"] += 1
                for hunk in changed_file.hunks:
                    content = "\n".join(hunk.lines)
                    if not content:
                        continue
                    pending.append(
                        _PendingSnippet(
                            role="diff",
                            path=changed_file.path,
                            start_line=hunk.new_start or 1,
                            end_line=max(hunk.new_start + max(hunk.new_count, 1) - 1, 1),
                            content=content,
                            language=None,
                            file_hash=diff.data.change_set_id,
                            reason=f"hunk {hunk.hunk_id}",
                            score=1.0,
                        )
                    )
            symbols = self._changed_symbols.execute(
                GetChangedSymbolsRequest(
                    change_set_id=request.change_set_id,
                    path=request.path,
                    max_symbols=50,
                )
            )
            warnings.extend(symbols.warnings)
            for symbol in symbols.data:
                coverage["changed_symbols"] += 1
                body = self._load_range(symbol.path, symbol.start_line, symbol.end_line)
                if body is None:
                    limitations.append(f"missing_source:{symbol.path}")
                    continue
                pending.append(
                    _PendingSnippet(
                        role="changed_symbol",
                        path=symbol.path,
                        start_line=symbol.start_line,
                        end_line=symbol.end_line,
                        content=body[0],
                        language=body[1],
                        file_hash=body[2],
                        reason=f"changed symbol {symbol.qualified_name}",
                        score=0.95,
                    )
                )
            impacts = self._find_impacts.execute(
                FindChangeImpactsRequest(
                    change_set_id=request.change_set_id,
                    path=request.path,
                    max_symbols=25,
                    max_references_per_symbol=20,
                    max_tests_per_symbol=8,
                )
            )
            warnings.extend(impacts.warnings)
            for impact in impacts.data:
                for caller in impact.callers[:5]:
                    coverage["callers"] += 1
                    body = self._load_range(
                        caller.path,
                        max(caller.start_line - 2, 1),
                        caller.end_line + 2,
                    )
                    if body is None:
                        continue
                    pending.append(
                        _PendingSnippet(
                            role="caller",
                            path=caller.path,
                            start_line=max(caller.start_line - 2, 1),
                            end_line=caller.end_line + 2,
                            content=body[0],
                            language=body[1],
                            file_hash=body[2],
                            reason=f"caller of {impact.qualified_name}",
                            score=caller.confidence,
                        )
                    )
                for test in impact.related_tests[:5]:
                    coverage["tests"] += 1
                    body = self._load_range(test.path, 1, 80)
                    if body is None:
                        continue
                    pending.append(
                        _PendingSnippet(
                            role="test",
                            path=test.path,
                            start_line=1,
                            end_line=80,
                            content=body[0],
                            language=body[1],
                            file_hash=body[2],
                            reason=f"related test ({test.reason})",
                            score=test.score,
                        )
                    )
                for config in impact.config_files[:3]:
                    coverage["configs"] += 1
                    body = self._load_range(
                        config.path,
                        max(config.start_line - 1, 1),
                        config.end_line + 1,
                    )
                    if body is None:
                        continue
                    pending.append(
                        _PendingSnippet(
                            role="config",
                            path=config.path,
                            start_line=max(config.start_line - 1, 1),
                            end_line=config.end_line + 1,
                            content=body[0],
                            language=body[1],
                            file_hash=body[2],
                            reason="configuration reference",
                            score=config.confidence,
                        )
                    )
            if not symbols.data:
                limitations.append("structural_index_required_for_symbol_context")
            limitations.append("commands_not_executed")
            snippets, omitted, estimated = _apply_budget(
                pending,
                max_tokens=request.max_tokens,
                reserved_tokens=request.reserved_tokens,
                max_files=request.max_files,
                max_snippets=request.max_snippets,
            )
            return ReviewContextBundle(
                change_set_id=request.change_set_id,
                snippets=snippets,
                estimated_tokens=estimated,
                available_tokens=request.max_tokens,
                omitted=omitted,
                limitations=tuple(dict.fromkeys(limitations)),
                coverage=coverage,
            )

        bundle, elapsed_ms = timed(resolve)
        return ToolResult(
            bundle,
            elapsed_ms,
            warnings=normalize_warnings(warnings, capability="review"),
        )

    def _load_range(
        self,
        path: str,
        start_line: int,
        end_line: int,
    ) -> tuple[str, str | None, str] | None:
        try:
            source = self._reader.load(path)
        except Exception:
            return None
        lines = source.content.splitlines()
        if not lines:
            return "", source.language, source.content_hash
        start = max(start_line, 1)
        end = min(max(end_line, start), len(lines))
        excerpt = "\n".join(lines[start - 1 : end])
        return excerpt, source.language, source.content_hash


def _apply_budget(
    pending: list[_PendingSnippet],
    *,
    max_tokens: int,
    reserved_tokens: int,
    max_files: int,
    max_snippets: int,
) -> tuple[tuple[ContextSnippet, ...], dict[str, int], int]:
    ordered = sorted(
        pending,
        key=lambda item: (
            _ROLE_PRIORITY.get(item.role, 99),
            -item.score,
            item.path,
            item.start_line,
        ),
    )
    selected: list[ContextSnippet] = []
    omitted = {
        "token_budget": 0,
        "file_limit": 0,
        "snippet_limit": 0,
        "duplicate": 0,
    }
    used_tokens = 0
    seen_files: set[str] = set()
    seen_keys: set[tuple[str, int, int, str]] = set()
    # Prefer spending reserved budget on diffs first.
    diff_budget = reserved_tokens
    remaining = max_tokens
    for item in ordered:
        key = (item.path, item.start_line, item.end_line, item.role)
        if key in seen_keys:
            omitted["duplicate"] += 1
            continue
        if len(selected) >= max_snippets:
            omitted["snippet_limit"] += 1
            continue
        if item.path not in seen_files and len(seen_files) >= max_files:
            omitted["file_limit"] += 1
            continue
        content = item.content
        tokens = estimate_tokens(content)
        budget = diff_budget if item.role == "diff" else remaining
        if tokens > budget:
            clipped = _clip(content, budget)
            if not clipped:
                omitted["token_budget"] += 1
                continue
            content = clipped
            tokens = estimate_tokens(content)
            truncated = True
        else:
            truncated = False
        if item.role == "diff":
            diff_budget = max(diff_budget - tokens, 0)
        remaining = max(remaining - tokens, 0)
        used_tokens += tokens
        seen_files.add(item.path)
        seen_keys.add(key)
        selected.append(
            ContextSnippet(
                snippet=CodeSnippet(
                    location=CodeLocation(
                        path=item.path,
                        start_line=item.start_line,
                        end_line=item.end_line,
                    ),
                    content=content,
                    language=item.language,
                    file_hash=item.file_hash,
                ),
                score=item.score,
                role=item.role,
                relation=None,
                depth=0,
                estimated_tokens=tokens,
                reason=item.reason,
                truncated=truncated,
            )
        )
        if remaining <= 0 and diff_budget <= 0:
            break
    return tuple(selected), omitted, used_tokens


def _clip(content: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    max_bytes = max_tokens * 3
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    if "\n" in truncated:
        truncated = truncated.rsplit("\n", 1)[0]
    return truncated
