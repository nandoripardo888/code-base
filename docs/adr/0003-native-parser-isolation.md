# ADR 0003: Native parser isolation

Status: accepted for phase three

Tree-sitter and grammar-native state will load only in supervised subprocesses.
Timeout, restart, circuit-breaker, and textual fallback behavior protect the main
CLI, API, indexing, and future server processes.

## Implemented decision

Parser requests cross a JSON subprocess boundary. The supervisor starts workers
without a shell, applies a per-file timeout, detects invalid output and abnormal
exit, terminates stuck children, caches failing payload identities, and opens a
per-language circuit after consecutive failures. Shutdown is idempotent.

Only `infrastructure/parsers/native_worker.py` may load Tree-sitter. Java and
Python grammars are optional package extras; PL/SQL uses a dedicated isolated
extractor. All responses contain serializable domain data rather than AST or
native objects.

A parser failure removes stale structure for the changed file, records a parser
failure, creates textual chunks, and lets the index finish with warnings. This
preserves CLI, API, FTS, Ripgrep, and direct file reads.
