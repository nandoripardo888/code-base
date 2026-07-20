from code_harness.domain.errors import ParserUnavailableError
from code_harness.domain.models.structural import AnalyzeRequest, AnalyzeResult
from code_harness.domain.protocols.structural_analyzer import StructuralAnalyzer


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
