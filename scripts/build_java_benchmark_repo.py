"""Build synthetic Java repositories for indexing benchmarks."""

from argparse import ArgumentParser
from pathlib import Path


def _java_source(
    index: int,
    *,
    target_bytes: int | None = None,
    calls_per_file: int = 0,
) -> str:
    name = f"BenchFile{index:05d}"
    calls = "".join(f"        helper{call_index % 10}();\n" for call_index in range(calls_per_file))
    helpers = "".join(
        f"    private void helper{helper_index}() {{ }}\n" for helper_index in range(10)
    )
    source = (
        f"package com.example.bench;\n\n"
        f"public class {name} {{\n"
        f"    private final int id = {index};\n\n"
        f"    public String name() {{\n"
        f'        return "{name}";\n'
        f"    }}\n\n"
        f"    public int id() {{\n"
        f"        return id;\n"
        f"    }}\n\n"
        f"    public void exerciseReferences() {{\n"
        f"{calls}"
        f"    }}\n\n"
        f"{helpers}"
        f"}}\n"
    )
    if target_bytes is not None and len(source.encode("utf-8")) < target_bytes:
        padding = target_bytes - len(source.encode("utf-8"))
        comment = "\n/*" + ("x" * max(0, padding - 5)) + "*/\n"
        source = source[:-2] + comment + "}\n"
    return source


def build_java_repository(
    destination: Path,
    count: int,
    *,
    target_bytes: int | None = None,
    calls_per_file: int = 0,
) -> None:
    if count < 1:
        raise ValueError("count must be >= 1")
    if target_bytes is not None and target_bytes < 1:
        raise ValueError("target_bytes must be >= 1")
    if calls_per_file < 0:
        raise ValueError("calls_per_file must be >= 0")
    destination.mkdir(parents=True, exist_ok=False)
    source_root = destination / "src" / "com" / "example" / "bench"
    source_root.mkdir(parents=True)
    for index in range(1, count + 1):
        path = source_root / f"BenchFile{index:05d}.java"
        path.write_text(
            _java_source(
                index,
                target_bytes=target_bytes,
                calls_per_file=calls_per_file,
            ),
            encoding="utf-8",
        )


def main() -> None:
    parser = ArgumentParser(description="Create a synthetic Java repository for benchmarks.")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, required=True, help="Number of Java files.")
    parser.add_argument(
        "--target-bytes",
        type=int,
        help="Approximate minimum UTF-8 bytes per generated file.",
    )
    parser.add_argument(
        "--calls-per-file",
        type=int,
        default=0,
        help="Method-call references generated in each Java file.",
    )
    arguments = parser.parse_args()
    build_java_repository(
        arguments.destination,
        arguments.count,
        target_bytes=arguments.target_bytes,
        calls_per_file=arguments.calls_per_file,
    )
    print(
        f"created {arguments.count} Java files at {arguments.destination} "
        f"target_bytes={arguments.target_bytes} calls_per_file={arguments.calls_per_file}"
    )


if __name__ == "__main__":
    main()
