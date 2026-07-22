# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Added capability statuses, structured warnings, strategy outcomes, and
  recoverable typed errors shared by Python, CLI, and MCP.
- Made `find_references` structural-first with optional Ripgrep and controlled
  degradation; `search_regex` remains Ripgrep-only with clearer doctor diagnosis.
- Defaulted outline/symbol responses to compact payloads without bodies; added
  `display_signature` / `canonical_signature` with schema migration v5.
- Separated `index_state` from `service_state`, exposed per-capability health, and
  cached semantic probe failures until config change or `doctor --deep`.
- Added limited camelCase/snake_case lexical expansion, query-oriented context
  windows, stable `list_files` pagination, read truncation metadata, and richer
  match evidence/spans.
- Added deterministic hybrid ranking across lexical, structural, path, reference,
  and optional semantic candidates.
- Added controlled context expansion with current-file validation and conservative
  token budgets.
- Added structured repository maps and exposed all phase-five capabilities through
  the Python API and CLI.

## 0.1.0 - 2026-07-18

- Added the layered Python package and CLI bootstrap.
- Added safe file discovery, path search, source reading, and range reading.
- Added literal and regular-expression search through Ripgrep.
- Added typed results, stable errors, project registration, and JSON output.
- Added Windows and Linux CI plus unit, integration, and architecture tests.
