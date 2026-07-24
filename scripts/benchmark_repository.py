"""Benchmark lexical search and structural indexing baselines."""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, Namespace
from datetime import UTC, datetime
from pathlib import Path
from shutil import rmtree
from time import perf_counter
from typing import Any

from code_harness import CodeHarness
from code_harness.domain.enums import IndexMode
from code_harness.interfaces.serialization import to_primitive


def _peak_rss_bytes() -> int | None:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB; macOS reports bytes.
        if sys.platform == "darwin":
            return int(usage)
        return int(usage) * 1024
    except (ImportError, AttributeError, OSError):
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
                wintypes.DWORD,
            ]
            get_process_memory_info.restype = wintypes.BOOL
            handle = kernel32.GetCurrentProcess()
            if get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        except (AttributeError, OSError, ValueError, TypeError):
            return None
    return None


def _wipe_index(repository: Path) -> None:
    index_dir = repository / ".code-harness"
    if index_dir.exists():
        rmtree(index_dir)


def _print_index_summary(payload: dict[str, Any]) -> None:
    report = payload["report"]
    timings = report.get("timings") or {}
    print(f"label={payload['label']}")
    print(f"mode={payload['mode']} transport={payload['transport']}")
    print(
        f"files discovered={report['discovered_files']} "
        f"indexed={report['indexed_files']} "
        f"unchanged={report['unchanged_files']}"
    )
    print(
        "timings_ms "
        f"discovery={timings.get('discovery_ms')} "
        f"read_hash={timings.get('read_hash_ms')} "
        f"worker_init={timings.get('worker_init_ms')} "
        f"analysis={timings.get('analysis_ms')} "
        f"chunks={timings.get('chunk_build_ms')} "
        f"embeddings={timings.get('embedding_ms')} "
        f"commit={timings.get('commit_ms')} "
        f"total={timings.get('total_ms')}"
    )
    print(
        "metrics "
        f"files_per_second={timings.get('files_per_second')} "
        f"processes_created={timings.get('processes_created')} "
        f"distinct_pids={timings.get('distinct_pids')} "
        f"restarts={timings.get('worker_restarts')} "
        f"timeouts={timings.get('worker_timeouts')} "
        f"parser_cache_hits={timings.get('parser_cache_hits')} "
        f"parser_workers={timings.get('parser_workers', payload.get('parser_workers'))} "
        f"max_concurrent={timings.get('max_concurrent_analyses')} "
        f"avg_file_bytes={timings.get('avg_file_bytes')} "
        f"analysis_p50={timings.get('analysis_ms_p50')} "
        f"analysis_p95={timings.get('analysis_ms_p95')} "
        f"analysis_p99={timings.get('analysis_ms_p99')} "
        f"peak_rss_bytes={payload.get('peak_rss_bytes')}"
    )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (percentile / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _summarize_metric(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "min": round(ordered[0], 3),
        "median": round(median, 3),
        "max": round(ordered[-1], 3),
        "p95": round(_percentile(ordered, 95), 3),
        "n": float(len(ordered)),
    }


def _run_index_once(arguments: Namespace) -> dict[str, Any]:
    repository = arguments.repository.resolve()
    if arguments.wipe_index:
        _wipe_index(repository)

    os.environ["CODE_HARNESS_PARSER_WORKERS"] = str(arguments.parser_workers)
    harness = CodeHarness.open(repository)
    try:
        mode = IndexMode(arguments.mode)
        wall_started = perf_counter()
        result = harness.index_project(mode=mode.value)
        wall_ms = (perf_counter() - wall_started) * 1000
        report = to_primitive(result.data)
        assert isinstance(report, dict)

        return {
            "kind": "index",
            "recorded_at": datetime.now(UTC).isoformat(),
            "label": arguments.label,
            "transport": arguments.transport,
            "parser_workers": arguments.parser_workers,
            "repository": str(repository),
            "mode": mode.value,
            "wall_ms": round(wall_ms, 3),
            "tool_elapsed_ms": result.elapsed_ms,
            "peak_rss_bytes": _peak_rss_bytes(),
            "report": report,
        }
    finally:
        harness.close()


def _run_index(arguments: Namespace) -> dict[str, Any]:
    repeats = max(1, int(getattr(arguments, "repeats", 1) or 1))
    discard_first = bool(getattr(arguments, "discard_first", False))
    total_runs = repeats + (1 if discard_first and repeats >= 1 else 0)

    runs: list[dict[str, Any]] = []
    for index in range(total_runs):
        role = "warmup" if discard_first and index == 0 else "measured"
        print(f"--- run {index + 1}/{total_runs} ({role}) workers={arguments.parser_workers} ---")
        payload = _run_index_once(arguments)
        payload["run_index"] = index
        payload["run_role"] = role
        _print_index_summary(payload)
        runs.append(payload)

    measured = [item for item in runs if item["run_role"] == "measured"]
    if not measured:
        measured = runs

    total_ms_values = [
        float((item["report"].get("timings") or {}).get("total_ms") or item["wall_ms"])
        for item in measured
    ]
    wall_ms_values = [float(item["wall_ms"]) for item in measured]
    files_per_second = [
        float((item["report"].get("timings") or {}).get("files_per_second") or 0.0)
        for item in measured
    ]
    processes = [
        int((item["report"].get("timings") or {}).get("processes_created") or 0)
        for item in measured
    ]
    restarts = [
        int((item["report"].get("timings") or {}).get("worker_restarts") or 0) for item in measured
    ]
    timeouts = [
        int((item["report"].get("timings") or {}).get("worker_timeouts") or 0) for item in measured
    ]

    summary = {
        "kind": "index_series",
        "recorded_at": datetime.now(UTC).isoformat(),
        "label": arguments.label,
        "transport": arguments.transport,
        "parser_workers": arguments.parser_workers,
        "repository": str(arguments.repository.resolve()),
        "mode": arguments.mode,
        "repeats": repeats,
        "discard_first": discard_first,
        "measured_runs": len(measured),
        "aggregate": {
            "total_ms": _summarize_metric(total_ms_values),
            "wall_ms": _summarize_metric(wall_ms_values),
            "files_per_second": _summarize_metric(files_per_second),
            "processes_created": {
                "min": min(processes) if processes else 0,
                "max": max(processes) if processes else 0,
            },
            "worker_restarts": {
                "min": min(restarts) if restarts else 0,
                "max": max(restarts) if restarts else 0,
            },
            "worker_timeouts": {
                "min": min(timeouts) if timeouts else 0,
                "max": max(timeouts) if timeouts else 0,
            },
        },
        "runs": runs,
    }

    agg = summary["aggregate"]["total_ms"]
    print(
        "aggregate_total_ms "
        f"min={agg['min']} median={agg['median']} max={agg['max']} p95={agg['p95']} "
        f"n={int(agg['n'])}"
    )

    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        payload_to_write: dict[str, Any] = summary if total_runs > 1 else measured[0]
        arguments.output.write_text(
            json.dumps(payload_to_write, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {arguments.output}")
    return summary if total_runs > 1 else measured[0]


def _run_lexical(arguments: Namespace) -> None:
    harness = CodeHarness.open(arguments.repository)

    started = perf_counter()
    files = harness.list_files()
    discovery_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    hits = harness.search_text(arguments.query)
    search_ms = (perf_counter() - started) * 1000

    print(f"files={len(files.data)} discovery_ms={discovery_ms:.2f}")
    print(f"hits={len(hits.data)} search_ms={search_ms:.2f}")


def main() -> None:
    parser = ArgumentParser(description="Record code-harness benchmark baselines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lexical = subparsers.add_parser("lexical", help="Lexical discovery and search baseline.")
    lexical.add_argument("repository", type=Path)
    lexical.add_argument("query")
    lexical.set_defaults(func=_run_lexical)

    index = subparsers.add_parser("index", help="Structural indexing baseline.")
    index.add_argument("repository", type=Path)
    index.add_argument(
        "--mode",
        choices=[item.value for item in IndexMode],
        default=IndexMode.FULL.value,
    )
    index.add_argument(
        "--label",
        default="one-shot-serial",
        help="Scenario label stored in the JSON output.",
    )
    index.add_argument(
        "--transport",
        default="one-shot",
        help="Transport label (one-shot today; persistent later).",
    )
    index.add_argument(
        "--parser-workers",
        type=int,
        default=1,
        help="Worker pool size (sets CODE_HARNESS_PARSER_WORKERS for this run).",
    )
    index.add_argument(
        "--wipe-index",
        action="store_true",
        help="Delete .code-harness before indexing (cold baseline).",
    )
    index.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of measured runs to record (default: 1).",
    )
    index.add_argument(
        "--discard-first",
        action="store_true",
        help="Run one extra warmup iteration and exclude it from aggregates.",
    )
    index.add_argument(
        "--output",
        type=Path,
        help="Optional JSON path for the recorded baseline.",
    )
    index.set_defaults(func=_run_index)

    arguments = parser.parse_args()
    arguments.func(arguments)


if __name__ == "__main__":
    main()
