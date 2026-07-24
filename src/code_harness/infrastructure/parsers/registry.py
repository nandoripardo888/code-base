from code_harness.domain.errors import ParserUnavailableError
from code_harness.domain.models.structural import AnalyzeRequest, AnalyzeResult
from code_harness.domain.protocols.structural_analyzer import StructuralAnalyzer
from code_harness.infrastructure.parsers.native_supervisor import ParserRuntimeMetrics


class StructuralAnalyzerRegistry:
    def __init__(self, analyzers: tuple[StructuralAnalyzer, ...]) -> None:
        self._analyzers = analyzers

    @property
    def name(self) -> str:
        return "structural-analyzer-registry"

    @property
    def version(self) -> str:
        versions = ",".join(f"{item.name}:{item.version}" for item in self._analyzers)
        return versions or "disabled"

    def supports(self, language: str) -> bool:
        return any(item.supports(language) for item in self._analyzers)

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        for analyzer in self._analyzers:
            if analyzer.supports(request.language):
                return analyzer.analyze(request)
        raise ParserUnavailableError(request.language)

    def health_check(self) -> bool:
        return all(item.health_check() for item in self._analyzers)

    def shutdown(self) -> None:
        for analyzer in self._analyzers:
            analyzer.shutdown()

    def metrics_snapshot(self) -> ParserRuntimeMetrics:
        processes_created = 0
        distinct_pids = 0
        restarts = 0
        timeouts = 0
        spawn_ms = 0
        request_ms = 0
        parser_cache_hits = 0
        worker_count = 0
        max_concurrent_analyses = 0
        for analyzer in self._analyzers:
            snapshot = getattr(analyzer, "metrics_snapshot", None)
            if not callable(snapshot):
                continue
            metrics = snapshot()
            processes_created += int(getattr(metrics, "processes_created", 0) or 0)
            distinct_pids += int(getattr(metrics, "distinct_pids", 0) or 0)
            restarts += int(getattr(metrics, "restarts", 0) or 0)
            timeouts += int(getattr(metrics, "timeouts", 0) or 0)
            spawn_ms += int(getattr(metrics, "spawn_ms", 0) or 0)
            request_ms += int(getattr(metrics, "request_ms", 0) or 0)
            parser_cache_hits += int(getattr(metrics, "parser_cache_hits", 0) or 0)
            worker_count += int(getattr(metrics, "worker_count", 0) or 0)
            max_concurrent_analyses = max(
                max_concurrent_analyses,
                int(getattr(metrics, "max_concurrent_analyses", 0) or 0),
            )
        return ParserRuntimeMetrics(
            processes_created=processes_created,
            distinct_pids=distinct_pids,
            restarts=restarts,
            timeouts=timeouts,
            spawn_ms=spawn_ms,
            request_ms=request_ms,
            parser_cache_hits=parser_cache_hits,
            worker_count=worker_count,
            max_concurrent_analyses=max_concurrent_analyses,
        )
