# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Added separately gated MCP process and PowerShell inspection/execution,
  polling and cancellation, with capability-negotiated local-interactive
  elicitation, exact one-time approvals bound to the confirming MCP session,
  escaped/redacted confirmation summaries, and no MCP approval administration.
- Added in-process asynchronous execution, polling, idempotent cancellation,
  crash-safe per-project concurrency slots, interrupted-run recovery, and a
  bounded in-memory result cache without persisting stdout/stderr.
- Added opt-in, approval-only PowerShell 7 execution with fixed `NoProfile` /
  `NonInteractive` arguments, protected temporary scripts, Job Object
  supervision, exact digests, cleanup, and sanitized auditing.
- Added single-use execution approvals bound to canonical command digests,
  sanitized SQLite auditing outside the workspace, output redaction, and local
  Python/CLI approval administration.
- Changed hybrid scoring to an absolute evidence/coverage formula, added
  anchor-first query planning, and stopped broad container symbols from merging
  with nested members.
- Changed context enumeration to consume bounded contiguous anchor blocks before
  considering global fallback, with consistent considered/selected/omitted counts.
- Added language-aware lexical reference classification, Java type/instantiation
  references, parser analysis version 5, runtime build identity, and correlated
  `internal_error` responses.
- Added structured truncation causes, removed FTS/Ripgrep duplicate false
  positives, and made result limits prove an additional unique result.
- Normalized repository-map path filters across slash styles and trailing
  separators.
- Added safe query coverage metadata and capped path/comment confidence.
- Added comment-aware lexical references with `include_comments`, plus an MCP
  enum schema for all five `response_detail` values.
- Added compact, budget-aware MCP and CLI JSON/JSONL response profiles, with
  explicit debug/full modes and safe query-focused hybrid snippets.
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
