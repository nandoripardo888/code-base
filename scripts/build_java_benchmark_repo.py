"""Build synthetic Java repositories for indexing benchmarks."""

from argparse import ArgumentParser
from pathlib import Path


def _java_source(index: int) -> str:
    name = f"BenchFile{index:04d}"
    return (
        f"package com.example.bench;\n\n"
        f"public class {name} {{\n"
        f"    private final int id = {index};\n\n"
        f"    public String name() {{\n"
        f'        return "{name}";\n'
        f"    }}\n\n"
        f"    public int id() {{\n"
        f"        return id;\n"
        f"    }}\n"
        f"}}\n"
    )


def build_java_repository(destination: Path, count: int) -> None:
    if count < 1:
        raise ValueError("count must be >= 1")
    destination.mkdir(parents=True, exist_ok=False)
    source_root = destination / "src" / "com" / "example" / "bench"
    source_root.mkdir(parents=True)
    for index in range(1, count + 1):
        path = source_root / f"BenchFile{index:04d}.java"
        path.write_text(_java_source(index), encoding="utf-8")


def main() -> None:
    parser = ArgumentParser(description="Create a synthetic Java repository for benchmarks.")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, required=True, help="Number of Java files.")
    arguments = parser.parse_args()
    build_java_repository(arguments.destination, arguments.count)
    print(f"created {arguments.count} Java files at {arguments.destination}")


if __name__ == "__main__":
    main()
