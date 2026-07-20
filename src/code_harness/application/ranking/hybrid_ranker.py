from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from code_harness.domain.enums import MatchType, QueryKind
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import (
    HybridSearchHit,
    QueryClassification,
    SearchEvidence,
)

_RRF_K = 60
_WEIGHTS: dict[QueryKind, dict[MatchType, float]] = {
    QueryKind.EXACT: {
        MatchType.EXACT: 1.0,
        MatchType.FULL_TEXT: 1.0,
        MatchType.SYMBOL: 1.0,
        MatchType.REFERENCE: 0.70,
        MatchType.SEMANTIC: 0.25,
        MatchType.PATH: 0.90,
    },
    QueryKind.MIXED: {
        MatchType.EXACT: 0.90,
        MatchType.FULL_TEXT: 0.90,
        MatchType.SYMBOL: 1.0,
        MatchType.REFERENCE: 0.70,
        MatchType.SEMANTIC: 0.80,
        MatchType.PATH: 0.70,
    },
    QueryKind.CONCEPTUAL: {
        MatchType.EXACT: 0.55,
        MatchType.FULL_TEXT: 0.55,
        MatchType.SYMBOL: 0.75,
        MatchType.REFERENCE: 0.40,
        MatchType.SEMANTIC: 1.0,
        MatchType.PATH: 0.35,
    },
}


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    snippet: CodeSnippet
    match_type: MatchType
    raw_score: float
    matched_terms: tuple[str, ...]
    reason: str
    source_id: str | None = None
    source_name: str | None = None


@dataclass(slots=True)
class _Aggregate:
    snippet: CodeSnippet
    evidence: list[SearchEvidence]
    matched_terms: list[str]
    reasons: list[str]
    fused_score: float


def _normalized(candidate: HybridCandidate) -> float:
    if candidate.match_type is MatchType.SEMANTIC:
        return min(1.0, max(0.0, (candidate.raw_score + 1.0) / 2.0))
    return min(1.0, max(0.0, candidate.raw_score))


def _overlaps(left: CodeLocation, right: CodeLocation) -> bool:
    return (
        left.path == right.path
        and left.start_line <= right.end_line
        and right.start_line <= left.end_line
    )


def _same_target(aggregate: _Aggregate, candidate: HybridCandidate) -> bool:
    if _overlaps(aggregate.snippet.location, candidate.snippet.location):
        return True
    candidate_ids = {candidate.source_id} if candidate.source_id else set()
    aggregate_ids = {item.source_id for item in aggregate.evidence if item.source_id}
    return bool(candidate_ids & aggregate_ids)


def _merge_location(left: CodeLocation, right: CodeLocation) -> CodeLocation:
    return CodeLocation(
        left.path,
        min(left.start_line, right.start_line),
        max(left.end_line, right.end_line),
    )


def _directory(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "."


class HybridRanker:
    def __init__(self, *, max_results_per_file: int = 5) -> None:
        if max_results_per_file <= 0:
            raise ValueError("max_results_per_file must be greater than zero")
        self._max_results_per_file = max_results_per_file

    def rank(
        self,
        query: str,
        classification: QueryClassification,
        candidates: tuple[HybridCandidate, ...],
        *,
        max_results: int,
    ) -> tuple[HybridSearchHit, ...]:
        ranked_by_type: dict[MatchType, list[HybridCandidate]] = {}
        for candidate in candidates:
            ranked_by_type.setdefault(candidate.match_type, []).append(candidate)
        for values in ranked_by_type.values():
            values.sort(
                key=lambda item: (
                    -item.raw_score,
                    item.snippet.location.path,
                    item.snippet.location.start_line,
                    item.snippet.location.end_line,
                )
            )

        enriched: list[tuple[HybridCandidate, SearchEvidence]] = []
        weights = _WEIGHTS[classification.kind]
        for match_type, values in ranked_by_type.items():
            weight = weights.get(match_type, 0.5)
            for rank, candidate in enumerate(values, start=1):
                normalized = _normalized(candidate)
                contribution = weight * (1.0 / (_RRF_K + rank)) * (0.5 + 0.5 * normalized)
                enriched.append(
                    (
                        candidate,
                        SearchEvidence(
                            match_type,
                            rank,
                            candidate.raw_score,
                            normalized,
                            contribution,
                            candidate.source_id,
                            candidate.source_name,
                        ),
                    )
                )
        enriched.sort(
            key=lambda item: (
                -item[1].contribution,
                item[0].snippet.location.path,
                item[0].snippet.location.start_line,
            )
        )

        aggregates: list[_Aggregate] = []
        for candidate, evidence in enriched:
            aggregate = next(
                (item for item in aggregates if _same_target(item, candidate)),
                None,
            )
            if aggregate is None:
                aggregates.append(
                    _Aggregate(
                        candidate.snippet,
                        [evidence],
                        list(candidate.matched_terms),
                        [candidate.reason],
                        evidence.contribution,
                    )
                )
                continue
            aggregate_has_non_path = any(
                item.match_type is not MatchType.PATH for item in aggregate.evidence
            )
            if candidate.match_type is MatchType.PATH and aggregate_has_non_path:
                pass
            elif candidate.match_type is not MatchType.PATH and not aggregate_has_non_path:
                aggregate.snippet = candidate.snippet
            else:
                aggregate.snippet = replace(
                    aggregate.snippet,
                    location=_merge_location(
                        aggregate.snippet.location,
                        candidate.snippet.location,
                    ),
                    content=aggregate.snippet.content + "\n" + candidate.snippet.content,
                )
            if all(item.match_type is not evidence.match_type for item in aggregate.evidence):
                aggregate.evidence.append(evidence)
                aggregate.fused_score += evidence.contribution
            aggregate.matched_terms.extend(candidate.matched_terms)
            aggregate.reasons.append(candidate.reason)

        folded_query = query.casefold()
        identifiers = {value.casefold() for value in classification.identifiers}
        for aggregate in aggregates:
            multiplier = 1.0
            names = {
                item.source_name.casefold()
                for item in aggregate.evidence
                if item.source_name is not None
            }
            if identifiers & names:
                multiplier += 0.25
            if folded_query and folded_query in aggregate.snippet.content.casefold():
                multiplier += 0.20
            if any(
                term.casefold() in aggregate.snippet.location.path.casefold()
                for term in classification.path_terms
            ):
                multiplier += 0.15
            if any(item.match_type is MatchType.SYMBOL for item in aggregate.evidence):
                multiplier += 0.10
            aggregate.fused_score *= multiplier

        aggregates.sort(
            key=lambda item: (
                -item.fused_score,
                item.snippet.location.path,
                item.snippet.location.start_line,
            )
        )
        maximum = aggregates[0].fused_score if aggregates else 1.0
        remaining = list(aggregates)
        selected: list[HybridSearchHit] = []
        file_counts: dict[str, int] = {}
        directory_counts: dict[str, int] = {}
        while remaining and len(selected) < max_results:
            eligible = [
                item
                for item in remaining
                if file_counts.get(item.snippet.location.path, 0) < self._max_results_per_file
            ]
            if not eligible:
                break
            chosen = max(
                eligible,
                key=lambda item: (
                    (item.fused_score / maximum)
                    * (0.75 ** file_counts.get(item.snippet.location.path, 0))
                    * (0.90 ** directory_counts.get(_directory(item.snippet.location.path), 0)),
                    -len(item.snippet.location.path),
                    item.snippet.location.path,
                    -item.snippet.location.start_line,
                ),
            )
            remaining.remove(chosen)
            path = chosen.snippet.location.path
            directory = _directory(path)
            adjusted = (
                (chosen.fused_score / maximum)
                * (0.75 ** file_counts.get(path, 0))
                * (0.90 ** directory_counts.get(directory, 0))
            )
            selected.append(
                HybridSearchHit(
                    chosen.snippet,
                    min(1.0, adjusted),
                    tuple(sorted(chosen.evidence, key=lambda item: item.match_type.value)),
                    tuple(dict.fromkeys(chosen.matched_terms)),
                    self._reason(chosen.evidence),
                )
            )
            file_counts[path] = file_counts.get(path, 0) + 1
            directory_counts[directory] = directory_counts.get(directory, 0) + 1
        return tuple(selected)

    @staticmethod
    def _reason(evidence: list[SearchEvidence]) -> str:
        kinds = {item.match_type for item in evidence}
        reasons: list[str] = []
        if MatchType.SYMBOL in kinds:
            reasons.append("Matching symbol definition")
        if MatchType.EXACT in kinds or MatchType.FULL_TEXT in kinds:
            reasons.append("current-file lexical match")
        if MatchType.REFERENCE in kinds:
            reasons.append("structural or textual reference")
        if MatchType.SEMANTIC in kinds:
            reasons.append("semantic similarity")
        if MatchType.PATH in kinds:
            reasons.append("matching repository path")
        return "; ".join(reasons) + "."
