from argparse import ArgumentParser
from pathlib import Path
from time import perf_counter

from code_harness import CodeHarness


def main() -> None:
    parser = ArgumentParser(description="Record lexical discovery and search baselines.")
    parser.add_argument("repository", type=Path)
    parser.add_argument("query")
    arguments = parser.parse_args()
    harness = CodeHarness.open(arguments.repository)

    started = perf_counter()
    files = harness.list_files()
    discovery_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    hits = harness.search_text(arguments.query)
    search_ms = (perf_counter() - started) * 1000

    print(f"files={len(files.data)} discovery_ms={discovery_ms:.2f}")
    print(f"hits={len(hits.data)} search_ms={search_ms:.2f}")


if __name__ == "__main__":
    main()
