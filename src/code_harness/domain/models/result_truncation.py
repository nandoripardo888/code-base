from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TruncationReason(StrEnum):
    RESULT_LIMIT = "result_limit"
    SNIPPET_LINE_LIMIT = "snippet_line_limit"
    SNIPPET_CHAR_LIMIT = "snippet_char_limit"
    TOKEN_BUDGET = "token_budget"
    CANDIDATE_LIMIT = "candidate_limit"
    FILE_LIMIT = "file_limit"
    EXPANSION_LIMIT = "expansion_limit"
    RESPONSE_BUDGET = "response_budget"


@dataclass(frozen=True, slots=True)
class ResultTruncation:
    results: bool = False
    snippets: bool = False
    candidates: bool = False
    budget_exhausted: bool = False
    reasons: tuple[TruncationReason, ...] = ()
    omitted_results: int | None = None

    @property
    def truncated(self) -> bool:
        return bool(
            self.results
            or self.snippets
            or self.candidates
            or self.budget_exhausted
            or self.reasons
        )


def truncation(
    *reasons: TruncationReason,
    results: bool = False,
    snippets: bool = False,
    candidates: bool = False,
    budget_exhausted: bool = False,
    omitted_results: int | None = None,
) -> ResultTruncation:
    return ResultTruncation(
        results=results,
        snippets=snippets,
        candidates=candidates,
        budget_exhausted=budget_exhausted,
        reasons=tuple(dict.fromkeys(reasons)),
        omitted_results=omitted_results,
    )


def merge_truncations(
    *values: ResultTruncation | None,
    omitted_results: int | None = None,
) -> ResultTruncation | None:
    present = tuple(value for value in values if value is not None and value.truncated)
    if not present and omitted_results is None:
        return None
    known_omitted = [
        value.omitted_results for value in present if value.omitted_results is not None
    ]
    merged_omitted = omitted_results
    if merged_omitted is None and known_omitted:
        merged_omitted = sum(known_omitted)
    return ResultTruncation(
        results=any(value.results for value in present),
        snippets=any(value.snippets for value in present),
        candidates=any(value.candidates for value in present),
        budget_exhausted=any(value.budget_exhausted for value in present),
        reasons=tuple(dict.fromkeys(reason for value in present for reason in value.reasons)),
        omitted_results=merged_omitted,
    )
