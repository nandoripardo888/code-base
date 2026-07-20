import os
import shutil
import tempfile
from pathlib import Path


def main() -> None:
    os.environ["CODE_HARNESS_SEMANTIC"] = "1"
    os.environ["CODE_HARNESS_PARSERS"] = "0"
    from code_harness import CodeHarness

    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "sample_repository"
    with tempfile.TemporaryDirectory(prefix="code-harness-semantic-") as temporary:
        project = Path(temporary) / "project"
        shutil.copytree(fixture, project)
        harness = CodeHarness.open(project)
        report = harness.index_project().data
        if report.embedding_failures or report.generated_embeddings == 0:
            raise RuntimeError(f"Semantic indexing failed: {report.warnings}")
        query = "como organizar compromissos do profissional"
        if harness.search_text(query).data:
            raise RuntimeError("Smoke query unexpectedly has a literal match.")
        hits = harness.semantic_search(query, max_results=4).data
        if not hits or "agenda" not in hits[0].snippet.location.path.casefold():
            raise RuntimeError("Semantic search did not rank an agenda source first.")
        print(
            f"semantic_smoke=pass generated={report.generated_embeddings} "
            f"top={hits[0].snippet.location.path} score={hits[0].score:.4f}"
        )


if __name__ == "__main__":
    main()
