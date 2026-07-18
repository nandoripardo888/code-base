# ADR 0003: Native parser isolation

Status: accepted for phase three

Tree-sitter and grammar-native state will load only in supervised subprocesses.
Timeout, restart, circuit-breaker, and textual fallback behavior protect the main
CLI, API, indexing, and future server processes.
