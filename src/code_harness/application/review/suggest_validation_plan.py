from __future__ import annotations

from pathlib import PurePosixPath

from code_harness.application.dto.review_requests import (
    FindChangeImpactsRequest,
    ListChangedFilesRequest,
    SuggestValidationPlanRequest,
)
from code_harness.application.review.find_change_impacts import FindChangeImpactsTool
from code_harness.application.review.list_changed_files import ListChangedFilesTool
from code_harness.application.tools._timing import timed
from code_harness.domain.models.capability import ToolWarning
from code_harness.domain.models.review import ValidationPlan
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.file_catalog import FileCatalog


class SuggestValidationPlanTool:
    def __init__(
        self,
        *,
        catalog: FileCatalog,
        list_changed_files: ListChangedFilesTool,
        find_impacts: FindChangeImpactsTool,
    ) -> None:
        self._catalog = catalog
        self._list_changed_files = list_changed_files
        self._find_impacts = find_impacts

    def execute(self, request: SuggestValidationPlanRequest) -> ToolResult[ValidationPlan]:
        warnings: list[str | ToolWarning] = []

        def resolve() -> ValidationPlan:
            changed = self._list_changed_files.execute(
                ListChangedFilesRequest(change_set_id=request.change_set_id)
            )
            warnings.extend(changed.warnings)
            files = changed.data
            if request.path is not None:
                files = tuple(
                    item
                    for item in files
                    if item.path == request.path or item.old_path == request.path
                )
            modules = tuple(
                sorted({PurePosixPath(item.path).as_posix() for item in files if not item.binary})
            )
            impacts = self._find_impacts.execute(
                FindChangeImpactsRequest(
                    change_set_id=request.change_set_id,
                    path=request.path,
                    max_symbols=40,
                    max_tests_per_symbol=request.max_test_files,
                )
            )
            warnings.extend(impacts.warnings)
            tests: dict[str, float] = {}
            for impact in impacts.data:
                for related in impact.related_tests:
                    tests[related.path] = max(tests.get(related.path, 0.0), related.score)
            candidate_tests = tuple(
                path
                for path, _score in sorted(tests.items(), key=lambda item: (-item[1], item[0]))[
                    : request.max_test_files
                ]
            )
            catalog_paths = {item.path for item in self._catalog.list_files()}
            suggested_commands: list[str] = []
            if candidate_tests:
                joined = " ".join(candidate_tests[:8])
                suggested_commands.append(f"pytest {joined}")
            elif "tests/unit" in catalog_paths or any(
                path.startswith("tests/") for path in catalog_paths
            ):
                suggested_commands.append("pytest tests/unit tests/integration")
            affected_dirs = sorted(
                {
                    str(PurePosixPath(path).parent).replace("\\", "/")
                    for path in modules
                    if PurePosixPath(path).parent.as_posix() not in {".", ""}
                }
            )
            static_checks: list[str] = []
            has_ruff = any(
                path in catalog_paths or path.endswith("ruff.toml")
                for path in ("pyproject.toml", "ruff.toml", ".ruff.toml")
            ) or any(path.endswith("ruff.toml") for path in catalog_paths)
            if has_ruff and affected_dirs:
                static_checks.append(f"ruff check {' '.join(affected_dirs[:8])}")
            elif has_ruff:
                static_checks.append("ruff check .")
            limitations = (
                "test_discovery_heuristic_only",
                "commands_not_executed",
                "structural_index_required_for_symbol_impacts",
            )
            return ValidationPlan(
                change_set_id=request.change_set_id,
                candidate_tests=candidate_tests,
                suggested_commands=tuple(suggested_commands),
                affected_modules=modules,
                static_checks=tuple(static_checks),
                limitations=limitations,
            )

        plan, elapsed_ms = timed(resolve)
        return ToolResult(
            plan,
            elapsed_ms,
            warnings=normalize_warnings(warnings, capability="review"),
        )
