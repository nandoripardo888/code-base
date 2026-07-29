import os
import sqlite3
import ssl
import sys
from math import isfinite
from pathlib import Path
from typing import Any, cast

from code_harness.domain.enums import DiagnosticStatus
from code_harness.domain.errors import CodeHarnessError
from code_harness.domain.models.index_report import (
    DiagnosticCheck,
    DoctorReport,
)
from code_harness.domain.protocols.capability_reporter import CapabilityReporter
from code_harness.domain.protocols.embedding_provider import EmbeddingProvider
from code_harness.domain.protocols.structural_analyzer import StructuralAnalyzer
from code_harness.infrastructure.execution.persistence.migrations import (
    SCHEMA_VERSION as EXECUTION_SCHEMA_VERSION,
)
from code_harness.infrastructure.persistence.migrations import SCHEMA_VERSION
from code_harness.infrastructure.ripgrep.discovery import probe_ripgrep


class LocalDiagnosticProvider:
    def __init__(
        self,
        root: Path,
        index_path: Path,
        ripgrep_executable: str,
        parser: StructuralAnalyzer | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        semantic_enabled: bool = False,
        model_cache_path: Path | None = None,
        capability_reporter: CapabilityReporter | None = None,
        execution_enabled: bool = False,
        execution_backend: str = "host_supervised",
        execution_home: Path | None = None,
        execution_store_path: Path | None = None,
        execution_allow_elevated: bool = False,
        execution_powershell_enabled: bool = False,
        execution_powershell_executable: str = "pwsh",
        mcp_expose_execution: bool = False,
    ) -> None:
        self._root = root
        self._index_path = index_path
        self._ripgrep_executable = ripgrep_executable
        self._parser = parser
        self._embedding_provider = embedding_provider
        self._semantic_enabled = semantic_enabled
        self._model_cache_path = model_cache_path
        self._capability_reporter = capability_reporter
        self._execution_enabled = execution_enabled
        self._execution_backend = execution_backend
        self._execution_home = execution_home
        self._execution_store_path = execution_store_path
        self._execution_allow_elevated = execution_allow_elevated
        self._execution_powershell_enabled = execution_powershell_enabled
        self._execution_powershell_executable = execution_powershell_executable
        self._mcp_expose_execution = mcp_expose_execution

    def run(self, *, deep: bool = False) -> DoctorReport:
        if deep and self._capability_reporter is not None:
            self._capability_reporter.invalidate_semantic_health()
            provider = self._embedding_provider
            if provider is not None and hasattr(provider, "invalidate_health_cache"):
                cast("Any", provider).invalidate_health_cache()
        checks = [
            DiagnosticCheck(
                "project",
                DiagnosticStatus.PASS if self._root.is_dir() else DiagnosticStatus.FAIL,
                "Project root is accessible."
                if self._root.is_dir()
                else "Project root is unavailable.",
            ),
            DiagnosticCheck(
                "write_permission",
                DiagnosticStatus.PASS if os.access(self._root, os.W_OK) else DiagnosticStatus.FAIL,
                "Project root is writable."
                if os.access(self._root, os.W_OK)
                else "Project root is not writable.",
            ),
            self._ripgrep_check(),
            DiagnosticCheck(
                "python_runtime",
                DiagnosticStatus.PASS,
                f"Python {sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro} with {ssl.OPENSSL_VERSION}.",
            ),
        ]
        checks.extend(self._database_checks())
        if self._parser is not None:
            parser_healthy = self._parser.health_check()
            checks.append(
                DiagnosticCheck(
                    "structural_parser",
                    DiagnosticStatus.PASS if parser_healthy else DiagnosticStatus.WARNING,
                    "Structural parser worker is healthy."
                    if parser_healthy
                    else (
                        "Structural parser worker is unavailable; lexical fallback remains active."
                    ),
                )
            )
        if self._model_cache_path is not None:
            writable_parent = _nearest_existing_parent(self._model_cache_path)
            cache_writable = os.access(writable_parent, os.W_OK)
            checks.append(
                DiagnosticCheck(
                    "semantic_cache",
                    DiagnosticStatus.PASS if cache_writable else DiagnosticStatus.WARNING,
                    f"Semantic model cache: {self._model_cache_path}.",
                )
            )
        checks.append(self._semantic_check(deep=deep))
        checks.extend(self._execution_checks())
        return DoctorReport(
            healthy=not any(check.status is DiagnosticStatus.FAIL for check in checks),
            checks=tuple(checks),
        )

    def _execution_checks(self) -> tuple[DiagnosticCheck, ...]:
        if not self._execution_enabled:
            return (
                DiagnosticCheck(
                    "execution",
                    DiagnosticStatus.PASS,
                    "Command execution is disabled.",
                ),
            )
        windows = os.name == "nt"
        home = self._execution_home
        home_parent = _nearest_existing_parent(home) if home is not None else self._root
        elevated = _is_windows_elevated() if windows else False
        job_object_available = _windows_job_object_available() if windows else False
        audit_check = self._execution_store_check()
        return (
            DiagnosticCheck(
                "execution_backend",
                DiagnosticStatus.PASS
                if windows and self._execution_backend == "host_supervised"
                else DiagnosticStatus.WARNING,
                "host_supervised backend is available."
                if windows and self._execution_backend == "host_supervised"
                else "Process execution requires the Windows host_supervised backend.",
            ),
            DiagnosticCheck(
                "execution_job_object",
                DiagnosticStatus.PASS if job_object_available else DiagnosticStatus.WARNING,
                "Windows Job Object API is available."
                if job_object_available
                else "Windows Job Object API is unavailable; execution cannot start.",
            ),
            DiagnosticCheck(
                "execution_home",
                DiagnosticStatus.PASS
                if os.access(home_parent, os.W_OK)
                else DiagnosticStatus.WARNING,
                f"Execution runtime directory: {home or self._root}.",
            ),
            audit_check,
            self._execution_powershell_check(),
            DiagnosticCheck(
                "execution_elevation",
                DiagnosticStatus.WARNING
                if elevated and not self._execution_allow_elevated
                else DiagnosticStatus.PASS,
                "Execution is blocked in the current elevated session."
                if elevated and not self._execution_allow_elevated
                else "Execution session elevation policy is satisfied.",
            ),
            DiagnosticCheck(
                "execution_mcp",
                DiagnosticStatus.WARNING if self._mcp_expose_execution else DiagnosticStatus.PASS,
                "MCP execution exposure is enabled; only connect trusted clients."
                if self._mcp_expose_execution
                else "MCP execution exposure is disabled.",
            ),
        )

    def _execution_powershell_check(self) -> DiagnosticCheck:
        if not self._execution_powershell_enabled:
            return DiagnosticCheck(
                "execution_powershell",
                DiagnosticStatus.PASS,
                "PowerShell execution is disabled.",
            )
        try:
            from code_harness.infrastructure.execution.analysis import PowerShellAstAnalyzer
            from code_harness.infrastructure.execution.runners import (
                PowerShell7ExecutableResolver,
            )

            executable = PowerShell7ExecutableResolver().resolve(
                self._execution_powershell_executable
            )
            PowerShellAstAnalyzer(executable).analyze("", timeout_seconds=5)
        except CodeHarnessError as error:
            return DiagnosticCheck(
                "execution_powershell",
                DiagnosticStatus.WARNING,
                error.message,
            )
        return DiagnosticCheck(
            "execution_powershell",
            DiagnosticStatus.PASS,
            f"PowerShell 7 execution is available: {executable}.",
        )

    def _execution_store_check(self) -> DiagnosticCheck:
        path = self._execution_store_path
        if path is None or not path.is_file():
            return DiagnosticCheck(
                "execution_audit_store",
                DiagnosticStatus.WARNING,
                "Execution audit store has not been initialized.",
            )
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as error:
            return DiagnosticCheck(
                "execution_audit_store",
                DiagnosticStatus.FAIL,
                f"Execution audit store check failed: {error}.",
            )
        healthy = integrity == "ok" and version == EXECUTION_SCHEMA_VERSION
        return DiagnosticCheck(
            "execution_audit_store",
            DiagnosticStatus.PASS if healthy else DiagnosticStatus.FAIL,
            (
                "Execution audit store "
                f"integrity={integrity}; schema={version}/{EXECUTION_SCHEMA_VERSION}."
            ),
        )

    def _ripgrep_check(self) -> DiagnosticCheck:
        probe = probe_ripgrep(self._ripgrep_executable)
        details = {
            "capability": "ripgrep",
            "configured_executable": probe.configured_executable,
            "resolved_path": probe.resolved_path,
            "version": probe.version,
            "execution_test": probe.execution_test,
            "root_cause": probe.root_cause,
            "remediation": list(probe.remediation),
            "affected_tools": list(probe.affected_tools),
            "unaffected_tools": list(probe.unaffected_tools),
        }
        if probe.execution_test == "passed":
            version = probe.version or "unknown"
            path = probe.resolved_path or probe.configured_executable
            return DiagnosticCheck(
                "ripgrep",
                DiagnosticStatus.PASS,
                f"Ripgrep is available ({version}) at {path}.",
                details,
            )
        cause = probe.root_cause or "Ripgrep is unavailable."
        return DiagnosticCheck(
            "ripgrep",
            DiagnosticStatus.FAIL,
            f"Ripgrep is unavailable: {cause}",
            details,
        )

    def _semantic_check(self, *, deep: bool) -> DiagnosticCheck:
        if not self._semantic_enabled:
            return DiagnosticCheck(
                "semantic",
                DiagnosticStatus.PASS,
                "Semantic search is disabled; lexical and structural search remain available.",
            )
        if self._embedding_provider is None:
            return DiagnosticCheck(
                "semantic",
                DiagnosticStatus.WARNING,
                "Semantic provider is not configured.",
            )
        try:
            identity = self._embedding_provider.identity
            if deep:
                vector = self._embedding_provider.embed_query(
                    "code harness semantic diagnostic probe"
                )
                if len(vector) != identity.dimensions or not all(isfinite(item) for item in vector):
                    raise ValueError("semantic probe returned an unexpected dimension")
                documents = (
                    "indice semantico com acao, validacao e Unicode",
                    "",
                    *(f"semantic batch diagnostic document {index}" for index in range(16)),
                )
                document_vectors = self._embedding_provider.embed_documents(documents)
                if len(document_vectors) != len(documents) or any(
                    len(item) != identity.dimensions or not all(isfinite(value) for value in item)
                    for item in document_vectors
                ):
                    raise ValueError("semantic batch probe returned invalid vectors")
        except (CodeHarnessError, ValueError) as error:
            message = error.message if isinstance(error, CodeHarnessError) else str(error)
            return DiagnosticCheck(
                "semantic",
                DiagnosticStatus.WARNING,
                f"Semantic provider is unavailable: {message}",
            )
        return DiagnosticCheck(
            "semantic",
            DiagnosticStatus.PASS,
            (
                f"Semantic provider loaded and inferred with {identity.model_id}."
                if deep
                else f"Semantic provider is configured for {identity.model_id}."
            ),
        )

    def _database_checks(self) -> tuple[DiagnosticCheck, ...]:
        if not self._index_path.is_file():
            return (
                DiagnosticCheck(
                    "sqlite",
                    DiagnosticStatus.WARNING,
                    "Index is not initialized; run init or index.",
                ),
            )
        try:
            uri = self._index_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                fts = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'file_fts'"
                ).fetchone()
        except sqlite3.DatabaseError as error:
            return (
                DiagnosticCheck("sqlite", DiagnosticStatus.FAIL, f"SQLite check failed: {error}."),
            )
        return (
            DiagnosticCheck(
                "sqlite_integrity",
                DiagnosticStatus.PASS if integrity == "ok" else DiagnosticStatus.FAIL,
                f"SQLite integrity check: {integrity}.",
            ),
            DiagnosticCheck(
                "schema_version",
                DiagnosticStatus.PASS if version == SCHEMA_VERSION else DiagnosticStatus.FAIL,
                f"Schema version is {version}; expected {SCHEMA_VERSION}.",
            ),
            DiagnosticCheck(
                "fts5",
                DiagnosticStatus.PASS if fts is not None else DiagnosticStatus.FAIL,
                "FTS5 index is available." if fts is not None else "FTS5 index is missing.",
            ),
        )


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while current != current.parent:
        try:
            if current.exists():
                return current
        except OSError:
            return current
        current = current.parent
    return current


def _is_windows_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):  # pragma: no cover - unavailable Windows API
        return True


def _windows_job_object_available() -> bool:
    try:
        import ctypes

        return hasattr(ctypes.WinDLL("kernel32"), "CreateJobObjectW")
    except (AttributeError, OSError):  # pragma: no cover - unavailable Windows API
        return False
