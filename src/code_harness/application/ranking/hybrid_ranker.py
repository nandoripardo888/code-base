from dataclasses import dataclass
from pathlib import PurePosixPath

from code_harness.domain.enums import MatchType
from code_harness.domain.models.code_chunk import CodeSnippet
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.hybrid import (
    HybridSearchHit,
    QueryClassification,
    QueryPlan,
    SearchEvidence,
    SearchScore,
)

_RRF_K = 60
_CONTAINER_KINDS = frozenset({"class", "interface", "enum", "record", "module", "package"})
_STRUCTURAL_REFERENCE_KINDS = frozenset(
    {"call", "definition", "import", "instantiation", "type_use"}
)


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    snippet: CodeSnippet
    match_type: MatchType
    raw_score: float
    matched_terms: tuple[str, ...]
    reason: str
    source_id: str | None = None
    source_name: str | None = None
    snippet_truncated: bool = False
    source_location: CodeLocation | None = None
    group_id: str | None = None
    scope: str = "fallback"
    comment_only: bool = False
    reference_kind: str | None = None
    symbol_kind: str | None = None
    match_line: int | None = None


@dataclass(slots=True)
class _Aggregate:
    snippet: CodeSnippet
    evidence: list[SearchEvidence]
    rrf_tiebreak: float
    snippet_truncated: bool
    source_location: CodeLocation | None
    scope: str
    comment_only: bool
    path_only: bool
    symbol_kinds: set[str]
    group_ids: set[str]


def _normalized(candidate: HybridCandidate) -> float:
    if candidate.match_type is MatchType.SEMANTIC:
        return min(1.0, max(0.0, (candidate.raw_score + 1.0) / 2.0))
    return min(1.0, max(0.0, candidate.raw_score))


def _base_weight(candidate: HybridCandidate) -> float:
    if candidate.comment_only or candidate.reference_kind == "comment_textual":
        return 0.25
    if candidate.reference_kind == "configuration_textual":
        return 0.45
    if candidate.match_type is MatchType.SYMBOL:
        return 0.95
    if candidate.match_type in {
        MatchType.EXACT,
        MatchType.EXACT_LITERAL,
        MatchType.FULL_TEXT,
        MatchType.FTS_PHRASE,
        MatchType.FTS_TERM,
        MatchType.SUBSTRING,
        MatchType.REGEX,
    }:
        return 0.90
    if candidate.match_type is MatchType.REFERENCE:
        if candidate.reference_kind in _STRUCTURAL_REFERENCE_KINDS:
            return 0.85
        if candidate.reference_kind == "configuration_textual":
            return 0.45
        return 0.65
    if candidate.match_type is MatchType.SEMANTIC:
        return 0.70
    if candidate.match_type is MatchType.PATH:
        return 0.45
    return 0.50


def _same_target(aggregate: _Aggregate, candidate: HybridCandidate) -> bool:
    if candidate.group_id is not None:
        return candidate.group_id in aggregate.group_ids
    location = aggregate.snippet.location
    other = candidate.snippet.location
    return (
        location.path == other.path
        and location.start_line == other.start_line
        and location.end_line == other.end_line
    )


def _directory(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "."


def _term_matches(content: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    folded = content.casefold()
    return tuple(term for term in terms if term and term.casefold() in folded)


def _query_terms(
    classification: QueryClassification,
    plan: QueryPlan | None,
) -> tuple[str, ...]:
    if plan is not None and plan.query_terms:
        return plan.query_terms
    values = classification.original_identifiers or classification.identifiers
    if not values:
        values = classification.lexical_terms
    return tuple(dict.fromkeys(values))


def _target_terms(
    classification: QueryClassification,
    plan: QueryPlan | None,
) -> tuple[str, ...]:
    if plan is not None:
        return plan.target_terms
    return classification.original_identifiers[1:]


def _merge_source_location(
    current: CodeLocation | None,
    candidate: CodeLocation | None,
) -> CodeLocation | None:
    if current is None:
        return candidate
    if candidate is None or current.path != candidate.path:
        return current
    return CodeLocation(
        current.path,
        min(current.start_line, candidate.start_line),
        max(current.end_line, candidate.end_line),
    )


def _prefer_candidate(aggregate: _Aggregate, candidate: HybridCandidate) -> bool:
    current_is_path = aggregate.path_only
    candidate_is_path = candidate.match_type is MatchType.PATH
    if current_is_path != candidate_is_path:
        return current_is_path
    current_lines = aggregate.snippet.location.end_line - aggregate.snippet.location.start_line + 1
    candidate_lines = (
        candidate.snippet.location.end_line - candidate.snippet.location.start_line + 1
    )
    return candidate_lines < current_lines


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
        plan: QueryPlan | None = None,
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

        enriched: list[tuple[HybridCandidate, SearchEvidence, float]] = []
        for match_type, values in ranked_by_type.items():
            for rank, candidate in enumerate(values, start=1):
                normalized = _normalized(candidate)
                strength = _base_weight(candidate) * normalized
                rrf = _base_weight(candidate) / (_RRF_K + rank)
                enriched.append(
                    (
                        candidate,
                        SearchEvidence(
                            match_type,
                            rank,
                            candidate.raw_score,
                            normalized,
                            strength,
                            candidate.source_id,
                            candidate.source_name,
                        ),
                        rrf,
                    )
                )
        enriched.sort(
            key=lambda item: (
                -item[2],
                item[0].snippet.location.path,
                item[0].snippet.location.start_line,
            )
        )

        aggregates: list[_Aggregate] = []
        for candidate, evidence, rrf in enriched:
            aggregate = next(
                (item for item in aggregates if _same_target(item, candidate)),
                None,
            )
            if aggregate is None:
                aggregates.append(
                    _Aggregate(
                        candidate.snippet,
                        [evidence],
                        rrf,
                        candidate.snippet_truncated,
                        candidate.source_location,
                        candidate.scope,
                        candidate.comment_only,
                        candidate.match_type is MatchType.PATH,
                        {candidate.symbol_kind} if candidate.symbol_kind else set(),
                        {candidate.group_id} if candidate.group_id else set(),
                    )
                )
                continue

            if _prefer_candidate(aggregate, candidate):
                aggregate.snippet = candidate.snippet
            aggregate.snippet_truncated = aggregate.snippet_truncated or candidate.snippet_truncated
            aggregate.source_location = _merge_source_location(
                aggregate.source_location,
                candidate.source_location,
            )
            aggregate.scope = (
                "anchor"
                if candidate.scope == "anchor" or aggregate.scope == "anchor"
                else "fallback"
            )
            aggregate.comment_only = aggregate.comment_only and candidate.comment_only
            aggregate.path_only = aggregate.path_only and candidate.match_type is MatchType.PATH
            if candidate.symbol_kind:
                aggregate.symbol_kinds.add(candidate.symbol_kind)
            if candidate.group_id:
                aggregate.group_ids.add(candidate.group_id)
            if all(item.match_type is not evidence.match_type for item in aggregate.evidence):
                aggregate.evidence.append(evidence)
                aggregate.rrf_tiebreak += rrf

        terms = _query_terms(classification, plan)
        targets = _target_terms(classification, plan)
        scored: list[tuple[HybridSearchHit, float, int]] = []
        for aggregate in aggregates:
            matched_terms = _term_matches(aggregate.snippet.content, terms)
            matched_targets = _term_matches(aggregate.snippet.content, targets)

            remaining_probability = 1.0
            for evidence in aggregate.evidence:
                remaining_probability *= 1.0 - min(1.0, max(0.0, evidence.contribution))
            evidence_score = 1.0 - remaining_probability

            if targets:
                anchor_covered = aggregate.scope == "anchor"
                if plan is not None and plan.anchor_name:
                    anchor_covered = anchor_covered or (
                        plan.anchor_name.casefold() in aggregate.snippet.content.casefold()
                    )
                coverage = (0.40 if anchor_covered else 0.0) + (
                    0.60 * len(matched_targets) / len(targets)
                )
            elif terms:
                coverage = len(matched_terms) / len(terms)
                if aggregate.scope == "anchor":
                    coverage = max(coverage, 1.0)
            else:
                coverage = 0.0

            scope_bonus = 1.0 if aggregate.scope == "anchor" else 0.0
            score = 0.55 * evidence_score + 0.35 * coverage + 0.10 * scope_bonus
            if aggregate.comment_only:
                score = min(score, 0.35)
            if aggregate.path_only:
                score = min(score, 0.45)
            if targets and not matched_targets:
                score = min(score, 0.60)
            score = min(1.0, max(0.0, score))

            if targets:
                if aggregate.scope == "anchor" and matched_targets:
                    phase = 0
                elif matched_targets:
                    phase = 1
                elif aggregate.scope == "anchor":
                    phase = 2
                else:
                    phase = 3
            else:
                phase = 0 if aggregate.scope == "anchor" else 1

            scored.append(
                (
                    HybridSearchHit(
                        aggregate.snippet,
                        score,
                        tuple(sorted(aggregate.evidence, key=lambda item: item.match_type.value)),
                        matched_terms,
                        self._reason(aggregate.evidence),
                        aggregate.snippet_truncated,
                        aggregate.source_location,
                        aggregate.scope,
                        coverage,
                        SearchScore(
                            evidence_score,
                            coverage,
                            scope_bonus,
                            aggregate.rrf_tiebreak,
                        ),
                        aggregate.comment_only,
                    ),
                    aggregate.rrf_tiebreak,
                    phase,
                )
            )

        remaining = list(scored)
        selected: list[HybridSearchHit] = []
        file_counts: dict[str, int] = {}
        directory_counts: dict[str, int] = {}
        while remaining and len(selected) < max_results:
            eligible = [
                item
                for item in remaining
                if item[0].scope == "anchor"
                or file_counts.get(item[0].snippet.location.path, 0) < self._max_results_per_file
            ]
            if not eligible:
                break

            def selection_key(
                item: tuple[HybridSearchHit, float, int],
            ) -> tuple[object, ...]:
                hit, rrf, phase = item
                path = hit.snippet.location.path
                diversity = (
                    1.0
                    if hit.scope == "anchor"
                    else (0.75 ** file_counts.get(path, 0))
                    * (0.90 ** directory_counts.get(_directory(path), 0))
                )
                return (
                    hit.comment_only,
                    all(evidence.match_type is MatchType.PATH for evidence in hit.evidence),
                    phase,
                    -(hit.score * diversity),
                    -rrf,
                    path,
                    hit.snippet.location.start_line,
                )

            chosen = min(eligible, key=selection_key)
            remaining.remove(chosen)
            hit = chosen[0]
            selected.append(hit)
            if hit.scope != "anchor":
                path = hit.snippet.location.path
                directory = _directory(path)
                file_counts[path] = file_counts.get(path, 0) + 1
                directory_counts[directory] = directory_counts.get(directory, 0) + 1
        return tuple(selected)

    @staticmethod
    def _reason(evidence: list[SearchEvidence]) -> str:
        kinds = {item.match_type for item in evidence}
        reasons: list[str] = []
        if MatchType.SYMBOL in kinds:
            reasons.append("matching symbol definition")
        if kinds & {
            MatchType.EXACT,
            MatchType.EXACT_LITERAL,
            MatchType.FULL_TEXT,
            MatchType.FTS_TERM,
            MatchType.FTS_PHRASE,
            MatchType.SUBSTRING,
        }:
            reasons.append("lexical match")
        if MatchType.REFERENCE in kinds:
            reasons.append("structural or textual reference")
        if MatchType.SEMANTIC in kinds:
            reasons.append("semantic similarity")
        if MatchType.PATH in kinds:
            reasons.append("matching repository path")
        return "; ".join(reasons).capitalize() + "."
