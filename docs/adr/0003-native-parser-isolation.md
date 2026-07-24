# ADR 0003: Native parser isolation

Status: accepted (updated for persistent worker pool)

Tree-sitter and grammar-native state load only in supervised subprocesses.
Timeout, restart, circuit-breaker, and textual fallback behavior protect the main
CLI, API, indexing, and MCP server processes.

## Implemented decision

Parser requests cross a versioned NDJSON subprocess boundary. The supervisor
owns a **pool of persistent workers** (not one disposable process per file).
Each slot handles a single request at a time; Tree-sitter `Language` / `Parser`
instances are cached inside each long-lived worker.

The supervisor:

- starts workers without a shell;
- applies a per-request timeout and restarts only the affected slot;
- detects invalid output, EOF, and abnormal exit;
- keeps a **shared** problem-payload cache and per-language circuit breaker
  (distinct failing payloads within a time window; not one breaker per slot);
- exposes idempotent `shutdown()` so CLI, MCP lifespan, and `atexit` can reclaim
  children without orphans.

Only `infrastructure/parsers/native_worker.py` may load Tree-sitter. Java and
Python grammars are optional package extras; PL/SQL uses a dedicated isolated
extractor. All responses contain serializable domain data rather than AST or
native objects.

The `IndexCoordinator` may run read/hash/analyze/chunk work concurrently through
a bounded `ThreadPoolExecutor`, but SQLite writes, progress callbacks, and final
report assembly stay on the coordinator thread. Outcomes are sorted before
commit so the on-disk index remains deterministic.

A parser failure removes stale structure for the changed file, records a parser
failure, creates textual chunks, and lets the index finish with warnings. This
preserves CLI, API, FTS, Ripgrep, and direct file reads.

Transport and pooling must not bump `ANALYSIS_VERSION` unless structural result
semantics change.

## Configuration

| Setting | Value |
|---------|--------|
| Environment | `CODE_HARNESS_PARSER_WORKERS` |
| Default | `min(4, os.cpu_count() or 1)` |
| Allowed range | `1`–`8` |

Four remains the default for memory and stability; eight is available when the
workload amortizes spawn and grammar-load cost (larger repositories).
