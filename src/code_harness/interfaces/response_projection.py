"""Compact, budget-aware projections for machine-readable interfaces."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from code_harness.domain.models.capability import CapabilityStatus, ToolWarning
from code_harness.domain.models.code_chunk import CodeSnippet, SourceRead
from code_harness.domain.models.code_location import CodeLocation
from code_harness.domain.models.context import ContextBundle, ContextSnippet
from code_harness.domain.models.file_listing import FileListingPage
from code_harness.domain.models.file_match import FileMatch
from code_harness.domain.models.hybrid import HybridSearchHit
from code_harness.domain.models.index_report import DoctorReport, IndexReport, IndexStatus
from code_harness.domain.models.repository_map import RepositoryMap
from code_harness.domain.models.result_truncation import (
    ResultTruncation,
    TruncationReason,
    merge_truncations,
    truncation,
)
from code_harness.domain.models.search_hit import SearchHit
from code_harness.domain.models.semantic import SemanticPreparationReport
from code_harness.domain.models.source_file import SourceFile
from code_harness.domain.models.structural import StructuralSearchResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.serialization import to_primitive

DEFAULT_MAX_DATA_CHARS = 30_000
_RESPONSE_DETAIL_ENV = "CODE_HARNESS_RESPONSE_DETAIL"


class ResponseDetail(StrEnum):
    MINIMAL = "minimal"
    COMPACT = "compact"
    DETAILED = "detailed"
    DEBUG = "debug"
    FULL = "full"


def resolve_response_detail(value: ResponseDetail | str | None = None) -> ResponseDetail:
    selected = value or os.environ.get(_RESPONSE_DETAIL_ENV, ResponseDetail.COMPACT.value)
    try:
        return (
            selected
            if isinstance(selected, ResponseDetail)
            else ResponseDetail(selected.casefold())
        )
    except ValueError as error:
        supported = ", ".join(item.value for item in ResponseDetail)
        raise ValueError(f"response_detail must be one of: {supported}") from error


def _location(location: CodeLocation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": location.path,
        "start_line": location.start_line,
        "end_line": location.end_line,
    }
    if location.start_column is not None:
        payload["start_column"] = location.start_column
    if location.end_column is not None:
        payload["end_column"] = location.end_column
    return payload


def _snippet(snippet: CodeSnippet, detail: ResponseDetail) -> dict[str, Any]:
    payload = _location(snippet.location)
    if snippet.content:
        payload["content"] = snippet.content
    if detail is ResponseDetail.DETAILED and snippet.language is not None:
        payload["language"] = snippet.language
    return payload


def _source_file(source: SourceFile, detail: ResponseDetail) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": source.path}
    if detail is not ResponseDetail.MINIMAL:
        payload["size_bytes"] = source.size_bytes
        if source.language is not None:
            payload["language"] = source.language
    if detail is ResponseDetail.DETAILED:
        payload["modified_at_ns"] = source.modified_at_ns
    return payload


def _search_hit(hit: SearchHit, detail: ResponseDetail) -> dict[str, Any]:
    payload = _snippet(hit.snippet, detail)
    if detail is not ResponseDetail.MINIMAL:
        payload["match_type"] = hit.match_type.value
        payload["score"] = hit.score
    if detail is ResponseDetail.DETAILED:
        payload["matched_terms"] = list(hit.matched_terms)
        if hit.reason is not None:
            payload["reason"] = hit.reason
        if hit.match_line is not None:
            payload["match_line"] = hit.match_line
        if hit.start_column is not None:
            payload["start_column"] = hit.start_column
        if hit.end_column is not None:
            payload["end_column"] = hit.end_column
        payload["validated"] = hit.validated
    return payload


def _hybrid_hit(hit: HybridSearchHit, detail: ResponseDetail) -> dict[str, Any]:
    payload = _snippet(hit.snippet, detail)
    if detail is not ResponseDetail.MINIMAL:
        payload["score"] = hit.score
        payload["match_types"] = sorted({item.match_type.value for item in hit.evidence})
        payload["matched_terms"] = list(hit.matched_terms)
        payload["scope"] = hit.scope
        if hit.snippet_truncated:
            payload["snippet_truncated"] = True
    if detail is ResponseDetail.DETAILED:
        payload["query_coverage"] = hit.query_coverage
        payload["reason"] = hit.reason
        if hit.score_components is not None:
            payload["score_components"] = to_primitive(hit.score_components)
        if hit.source_location is not None:
            payload["source_location"] = _location(hit.source_location)
    return payload


def _structural_result(
    result: StructuralSearchResult,
    detail: ResponseDetail,
) -> dict[str, Any]:
    if result.symbol is not None:
        symbol = result.symbol
        payload = {
            "name": symbol.name,
            "kind": symbol.kind,
            **_location(symbol.location),
        }
        if detail is not ResponseDetail.MINIMAL:
            if symbol.qualified_name is not None:
                payload["qualified_name"] = symbol.qualified_name
            if symbol.signature is not None:
                payload["signature"] = symbol.signature
        if detail is ResponseDetail.DETAILED and symbol.canonical_signature is not None:
            payload["canonical_signature"] = symbol.canonical_signature
    elif result.reference is not None:
        reference = result.reference
        payload = {
            "target_name": reference.target_name,
            "kind": reference.kind,
            **_location(reference.location),
        }
        if detail is not ResponseDetail.MINIMAL:
            payload["source"] = reference.source
        if detail is ResponseDetail.DETAILED:
            payload["confidence"] = reference.confidence
            payload["validated"] = reference.validated
            payload["resolution"] = reference.resolution
    else:
        payload = {}
    if result.content:
        payload["content"] = result.content
    return payload


def _source_read(source: SourceRead, detail: ResponseDetail) -> dict[str, Any]:
    payload = _location(source.snippet.location)
    if source.numbered_lines is not None:
        payload["lines"] = [list(item) for item in source.numbered_lines]
    else:
        payload["content"] = source.snippet.content
    if source.truncated:
        payload["truncated"] = True
    if detail in {ResponseDetail.DETAILED, ResponseDetail.DEBUG}:
        if source.snippet.language is not None:
            payload["language"] = source.snippet.language
        if source.requested_range is not None:
            payload["requested_range"] = list(source.requested_range)
        if source.actual_range is not None:
            payload["actual_range"] = list(source.actual_range)
        if source.total_lines is not None:
            payload["total_lines"] = source.total_lines
        if source.truncation is not None:
            payload["truncation"] = _strip_empty(to_primitive(source.truncation))
        if source.warnings:
            payload["warnings"] = list(source.warnings)
    if detail is ResponseDetail.DEBUG:
        payload["file_hash"] = source.snippet.file_hash
    return payload


def _file_match(match: FileMatch, detail: ResponseDetail) -> dict[str, Any]:
    payload = _source_file(match.source_file, detail)
    if detail is not ResponseDetail.MINIMAL:
        payload["score"] = match.score
    if detail is ResponseDetail.DETAILED:
        payload["reason"] = match.reason
        if match.match is not None:
            payload["match"] = _strip_empty(to_primitive(match.match))
    return payload


def _context_snippet(item: ContextSnippet, detail: ResponseDetail) -> dict[str, Any]:
    payload = _snippet(item.snippet, detail)
    payload["role"] = item.role
    if detail is not ResponseDetail.MINIMAL:
        payload["score"] = item.score
        if item.truncated:
            payload["truncated"] = True
    if detail is ResponseDetail.DETAILED:
        if item.relation is not None:
            payload["relation"] = item.relation
        payload["depth"] = item.depth
        payload["estimated_tokens"] = item.estimated_tokens
        payload["reason"] = item.reason
        payload["source_match_types"] = [value.value for value in item.source_match_types]
        if item.ranges:
            payload["ranges"] = [list(value) for value in item.ranges]
    return payload


def _context_bundle(bundle: ContextBundle, detail: ResponseDetail) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "snippets": [_context_snippet(item, detail) for item in bundle.snippets],
        "omitted_results": bundle.omitted_results,
        "estimated_tokens": bundle.estimated_tokens,
        "available_tokens": bundle.available_tokens,
    }
    if bundle.results_truncated:
        payload["results_truncated"] = True
    if bundle.candidates_truncated:
        payload["candidates_truncated"] = True
    if bundle.snippet_truncated:
        payload["snippet_truncated"] = True
    if bundle.budget_exhausted:
        payload["budget_exhausted"] = True
    if bundle.expansion_limited:
        payload["expansion_limited"] = True
    if bundle.omitted:
        payload["omitted"] = bundle.omitted
    has_limits = bool(
        bundle.omitted_results
        or bundle.results_truncated
        or bundle.candidates_truncated
        or bundle.snippet_truncated
        or bundle.budget_exhausted
        or bundle.expansion_limited
    )
    if detail is ResponseDetail.DETAILED or (detail is ResponseDetail.COMPACT and has_limits):
        payload.update(
            {
                "considered_results": bundle.considered_results,
                "selected_results": bundle.selected_results,
            }
        )
    if detail is ResponseDetail.DETAILED:
        payload["query"] = bundle.query
        if bundle.warnings:
            payload["warnings"] = list(bundle.warnings)
    return payload


def _capability(
    capability: CapabilityStatus,
    detail: ResponseDetail,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": capability.name,
        "state": capability.state.value,
    }
    if detail is ResponseDetail.DETAILED:
        payload["optional"] = capability.optional
        payload["enabled"] = capability.enabled
        if capability.root_cause is not None:
            payload["root_cause"] = capability.root_cause
        if capability.remediation is not None:
            payload["remediation"] = capability.remediation
    return payload


def _index_status(status: IndexStatus, detail: ResponseDetail) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": status.state.value,
        "service_version": status.service_version,
    }
    if detail is not ResponseDetail.MINIMAL:
        payload.update(
            {
                "file_count": status.file_count,
                "symbol_count": status.symbol_count,
                "reference_count": status.reference_count,
                "chunk_count": status.chunk_count,
                "warning_files": status.warning_files,
                "build_commit": status.build_commit,
                "service_started_at": status.service_started_at,
                "service_instance_id": status.service_instance_id,
            }
        )
        degraded = [
            item for item in status.capabilities if item.state.value not in {"ready", "disabled"}
        ]
        if degraded:
            payload["capabilities"] = [_capability(item, detail) for item in degraded]
    if detail is ResponseDetail.DETAILED:
        payload.update(
            {
                "schema_version": status.schema_version,
                "fts_document_count": status.fts_document_count,
                "parser_failure_count": status.parser_failure_count,
                "structural_schema_ready": status.structural_schema_ready,
                "semantic_schema_ready": status.semantic_schema_ready,
                "embedding_count": status.embedding_count,
                "embedded_chunk_count": status.embedded_chunk_count,
                "service_state": status.service_state,
                "capabilities": [_capability(item, detail) for item in status.capabilities],
                "last_run": to_primitive(status.last_run),
            }
        )
        if status.warnings:
            payload["warnings"] = list(status.warnings)
    cleaned = _strip_empty(payload)
    assert isinstance(cleaned, dict)
    return cleaned


def _index_report(report: IndexReport, detail: ResponseDetail) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": report.mode.value,
        "state": report.state.value,
        "indexed_files": report.indexed_files,
        "warning_files": report.warning_files,
    }
    if detail is not ResponseDetail.MINIMAL:
        payload.update(
            {
                "discovered_files": report.discovered_files,
                "new_files": report.new_files,
                "changed_files": report.changed_files,
                "removed_files": report.removed_files,
                "unchanged_files": report.unchanged_files,
                "indexed_symbols": report.indexed_symbols,
                "indexed_references": report.indexed_references,
                "indexed_chunks": report.indexed_chunks,
                "parser_failures": report.parser_failures,
                "generated_embeddings": report.generated_embeddings,
                "reused_embeddings": report.reused_embeddings,
                "embedding_failures": report.embedding_failures,
                "partial": report.partial,
            }
        )
        if report.partial:
            payload.update(
                {
                    "include_globs": list(report.include_globs),
                    "exclude_globs": list(report.exclude_globs),
                    "scoped_discovered_files": report.scoped_discovered_files,
                    "preserved_out_of_scope_files": report.preserved_out_of_scope_files,
                }
            )
    if detail is ResponseDetail.DETAILED:
        payload.update(_without_internal(report))
    cleaned = _strip_empty(payload)
    assert isinstance(cleaned, dict)
    return cleaned


def _doctor_report(report: DoctorReport, detail: ResponseDetail) -> dict[str, Any]:
    payload: dict[str, Any] = {"healthy": report.healthy}
    if detail is not ResponseDetail.MINIMAL:
        payload["checks"] = [
            {
                "name": check.name,
                "status": check.status.value,
                "message": check.message,
                **(
                    {"details": _strip_empty(to_primitive(check.details))}
                    if detail is ResponseDetail.DETAILED and check.details
                    else {}
                ),
            }
            for check in report.checks
        ]
    return payload


def _semantic_preparation(
    report: SemanticPreparationReport,
    detail: ResponseDetail,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ready": report.ready,
        "provider": report.provider,
        "model_id": report.model_id,
    }
    if detail is not ResponseDetail.MINIMAL:
        payload.update(
            {
                "provider_version": report.provider_version,
                "dimensions": report.dimensions,
                "strategy": report.strategy,
            }
        )
    if detail is ResponseDetail.DETAILED:
        payload["cache_path"] = report.cache_path
    return payload


_INTERNAL_KEYS = {
    "chunk_id",
    "content_hash",
    "file_hash",
    "parent_chunk_id",
    "parent_symbol_id",
    "project_id",
    "reference_id",
    "source_id",
    "source_symbol_id",
    "symbol_id",
    "target_symbol_id",
}


def _without_internal(value: Any) -> Any:
    primitive = to_primitive(value)
    if isinstance(primitive, dict):
        return {
            key: _without_internal(item)
            for key, item in primitive.items()
            if key not in _INTERNAL_KEYS
        }
    if isinstance(primitive, list):
        return [_without_internal(item) for item in primitive]
    return primitive


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        payload = {key: _strip_empty(item) for key, item in value.items()}
        return {
            key: item
            for key, item in payload.items()
            if item is not None and item != [] and item != {} and item != ()
        }
    if isinstance(value, list):
        return [_strip_empty(item) for item in value]
    return value


def project_value(value: Any, detail: ResponseDetail | str) -> Any:
    selected = resolve_response_detail(detail)
    if selected is ResponseDetail.FULL:
        return to_primitive(value)
    if isinstance(value, SourceRead):
        return _source_read(value, selected)
    if selected is ResponseDetail.DEBUG:
        return _strip_empty(to_primitive(value))
    if isinstance(value, SearchHit):
        return _search_hit(value, selected)
    if isinstance(value, HybridSearchHit):
        return _hybrid_hit(value, selected)
    if isinstance(value, StructuralSearchResult):
        return _structural_result(value, selected)
    if isinstance(value, FileListingPage):
        payload: dict[str, Any] = {
            "items": [_source_file(item, selected) for item in value.items],
        }
        if selected is not ResponseDetail.MINIMAL:
            payload.update(
                {
                    "total_count": value.total_count,
                    "next_cursor": value.next_cursor,
                    "sort": value.sort,
                    "sort_direction": value.sort_direction,
                }
            )
        return _strip_empty(payload)
    if isinstance(value, SourceFile):
        return _source_file(value, selected)
    if isinstance(value, FileMatch):
        return _file_match(value, selected)
    if isinstance(value, ContextBundle):
        return _strip_empty(_context_bundle(value, selected))
    if isinstance(value, ContextSnippet):
        return _strip_empty(_context_snippet(value, selected))
    if isinstance(value, IndexStatus):
        return _index_status(value, selected)
    if isinstance(value, IndexReport):
        return _index_report(value, selected)
    if isinstance(value, DoctorReport):
        return _doctor_report(value, selected)
    if isinstance(value, SemanticPreparationReport):
        return _semantic_preparation(value, selected)
    if isinstance(value, RepositoryMap):
        return _strip_empty(_without_internal(value))
    if isinstance(value, CodeSnippet):
        return _snippet(value, selected)
    if isinstance(value, CodeLocation):
        return _location(value)
    if isinstance(value, ToolWarning):
        return _strip_empty(to_primitive(value))
    if isinstance(value, (tuple, list)):
        return [project_value(item, selected) for item in value]
    if isinstance(value, dict):
        return _strip_empty(
            {str(key): project_value(item, selected) for key, item in value.items()}
        )
    return _strip_empty(_without_internal(value))


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def _primary_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value if value else None
    if isinstance(value, dict):
        preferred = (
            "items",
            "snippets",
            "matches",
            "files",
            "symbols",
            "directories",
            "lines",
            "capabilities",
            "checks",
        )
        for key in preferred:
            item = value.get(key)
            if isinstance(item, list) and item:
                return item
        for item in reversed(tuple(value.values())):
            if isinstance(item, dict):
                nested = _primary_list(item)
                if nested is not None:
                    return nested
    return None


def _any_nonempty_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value if value else None
    if isinstance(value, dict):
        for item in reversed(tuple(value.values())):
            nested = _any_nonempty_list(item)
            if nested is not None:
                return nested
    return None


def _longest_content(value: Any) -> tuple[dict[str, Any], str] | None:
    candidates: list[tuple[int, dict[str, Any], str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "content" and isinstance(item, str):
                candidates.append((len(item), value, key))
            else:
                nested = _longest_content(item)
                if nested is not None:
                    container, nested_key = nested
                    candidates.append((len(container[nested_key]), container, nested_key))
    elif isinstance(value, list):
        for item in value:
            nested = _longest_content(item)
            if nested is not None:
                container, nested_key = nested
                candidates.append((len(container[nested_key]), container, nested_key))
    if not candidates:
        return None
    _, container, key = max(candidates, key=lambda item: item[0])
    return container, key


def _trim_content(content: str, excess: int) -> str:
    target = max(0, len(content) - max(excess, 1))
    if target == 0:
        return ""
    candidate = content[:target]
    boundary = max(candidate.rfind("\n"), candidate.rfind("\r"))
    return candidate[: boundary + 1] if boundary >= 0 else candidate


def _repository_file_count(value: Any) -> int:
    if isinstance(value, list):
        return sum(_repository_file_count(item) for item in value)
    if not isinstance(value, dict):
        return 0
    if {"name", "path", "size_bytes"} <= value.keys():
        return 1
    return sum(_repository_file_count(item) for item in value.values())


def _sync_budget_metadata(data: Any, removed: list[Any], content_trimmed: bool) -> int:
    omitted = len(removed) + int(content_trimmed)
    if not isinstance(data, dict):
        removed_files = sum(_repository_file_count(item) for item in removed)
        return max(omitted, removed_files)
    if "snippets" in data and "omitted_results" in data:
        data["omitted_results"] = int(data["omitted_results"]) + len(removed)
        if "selected_results" in data:
            data["selected_results"] = max(0, int(data["selected_results"]) - len(removed))
        omitted_by_reason = data.setdefault("omitted", {}) if removed else data.get("omitted")
        if isinstance(omitted_by_reason, dict) and removed:
            omitted_by_reason["response_budget"] = int(
                omitted_by_reason.get("response_budget", 0)
            ) + len(removed)
    removed_files = sum(_repository_file_count(item) for item in removed)
    if removed_files and {"included_files", "omitted_files"} <= data.keys():
        data["included_files"] = max(0, int(data["included_files"]) - removed_files)
        data["omitted_files"] = int(data["omitted_files"]) + removed_files
    if "lines" in data and isinstance(data["lines"], list):
        lines = data["lines"]
        if lines:
            data["end_line"] = int(lines[-1][0])
        else:
            data["end_line"] = data["start_line"]
        data["truncated"] = True
    elif content_trimmed and isinstance(data.get("content"), str):
        content = data["content"]
        line_count = content.count("\n") + int(bool(content) and not content.endswith("\n"))
        data["end_line"] = max(
            int(data["start_line"]),
            int(data["start_line"]) + max(0, line_count - 1),
        )
        data["truncated"] = True
    return max(omitted, removed_files)


@dataclass(frozen=True, slots=True)
class BudgetedData:
    data: Any
    truncated: bool
    omitted_results: int
    results_truncated: bool = False
    snippets_truncated: bool = False


def apply_data_budget(data: Any, max_chars: int = DEFAULT_MAX_DATA_CHARS) -> BudgetedData:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if _json_chars(data) <= max_chars:
        return BudgetedData(data, False, 0)
    removed: list[Any] = []
    content_trimmed = False
    while _json_chars(data) > max_chars:
        sequence = _primary_list(data)
        if sequence is not None:
            removed.append(sequence.pop())
            continue
        target = _longest_content(data)
        if target is not None:
            container, key = target
            before = container[key]
            container[key] = _trim_content(before, _json_chars(data) - max_chars)
            if container[key] == before:
                break
            content_trimmed = True
            continue
        sequence = _any_nonempty_list(data)
        if sequence is None:
            break
        removed.append(sequence.pop())
    omitted = _sync_budget_metadata(data, removed, content_trimmed)
    return BudgetedData(
        data,
        True,
        omitted,
        results_truncated=bool(removed),
        snippets_truncated=content_trimmed,
    )


def _truncation_payload(value: ResultTruncation) -> dict[str, Any]:
    payload: dict[str, Any] = {"reasons": [reason.value for reason in value.reasons]}
    if value.results:
        payload["results"] = True
    if value.snippets:
        payload["snippets"] = True
    if value.candidates:
        payload["candidates"] = True
    if value.budget_exhausted:
        payload["budget_exhausted"] = True
    if value.omitted_results is not None:
        payload["omitted_results"] = value.omitted_results
    cleaned = _strip_empty(payload)
    assert isinstance(cleaned, dict)
    return cleaned


def serialize_projected_result(
    result: ToolResult[Any],
    detail: ResponseDetail | str | None = None,
) -> dict[str, Any]:
    selected = resolve_response_detail(detail)
    if selected is ResponseDetail.FULL:
        full_payload = to_primitive(result)
        assert isinstance(full_payload, dict)
        return full_payload

    data = project_value(result.data, selected)
    if (
        isinstance(result.data, ContextBundle)
        and selected is ResponseDetail.COMPACT
        and isinstance(data, dict)
        and _json_chars(data) > DEFAULT_MAX_DATA_CHARS
    ):
        data.setdefault("considered_results", result.data.considered_results)
        data.setdefault("selected_results", result.data.selected_results)
    budgeted = apply_data_budget(data)
    budget_truncation = (
        truncation(
            TruncationReason.RESPONSE_BUDGET,
            results=budgeted.results_truncated,
            snippets=budgeted.snippets_truncated,
            budget_exhausted=True,
            omitted_results=budgeted.omitted_results or None,
        )
        if budgeted.truncated
        else None
    )
    result_truncation = merge_truncations(result.truncation, budget_truncation)
    response_payload: dict[str, Any] = {"data": budgeted.data}
    if result.truncated or budgeted.truncated:
        response_payload["truncated"] = True
    if result_truncation is not None:
        response_payload["truncation"] = _truncation_payload(result_truncation)
        if result_truncation.omitted_results:
            response_payload["omitted_results"] = result_truncation.omitted_results
    if result.warnings:
        response_payload["warnings"] = [project_value(item, selected) for item in result.warnings]
    if selected in {ResponseDetail.MINIMAL, ResponseDetail.COMPACT}:
        if result.index_state not in {None, "ready"}:
            response_payload["index_state"] = result.index_state
    elif selected is ResponseDetail.DETAILED:
        if result.index_state is not None:
            response_payload["index_state"] = result.index_state
    elif selected is ResponseDetail.DEBUG:
        diagnostics: dict[str, Any] = {"elapsed_ms": result.elapsed_ms}
        if result.index_state is not None:
            diagnostics["index_state"] = result.index_state
        if result.strategies:
            diagnostics["strategies"] = _strip_empty(to_primitive(result.strategies))
        response_payload["diagnostics"] = diagnostics
    return response_payload
