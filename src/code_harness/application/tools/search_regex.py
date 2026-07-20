from code_harness.application.dto.requests import SearchRegexRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.text_searcher import TextSearcher


class SearchRegexTool:
    def __init__(self, searcher: TextSearcher) -> None:
        self._searcher = searcher

    def execute(self, request: SearchRegexRequest) -> ToolResult[tuple[SearchHit, ...]]:
        outcome, elapsed_ms = timed(
            lambda: self._searcher.search(
                query=request.query,
                regex=True,
                include_globs=request.include_globs,
                exclude_globs=request.exclude_globs,
                case_sensitive=request.case_sensitive,
                max_results=request.max_results,
                context_lines=request.context_lines,
                timeout_seconds=request.timeout_seconds,
            )
        )
        return ToolResult(
            outcome.hits,
            elapsed_ms,
            truncated=outcome.truncated,
            warnings=outcome.warnings,
            index_state=outcome.index_state,
        )
