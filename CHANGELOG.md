# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Changed

- `Grep` and `Glob` share an internal `search_core` module. MCP tool names and
  behaviour are unchanged; see `docs/search-improvements.md`.
- `Grep` and `Glob` default to **source-first** search: harness noise
  (`.code-harness/`, common caches, `*.err`) and optional `.code-harnessignore`
  are skipped. Pass `include_all=true` to search everything.
- `Grep` `content` mode groups matches by file (path heading + line text) and
  prints `N matches in M files` when more than one file is shown.
- `Glob` / Grep `glob` expand brace patterns (`*.{py,md}`) and accept a list of
  patterns; malformed braces raise `InvalidArgumentError`.
- Empty `Grep` / `Glob` results append a short generic `Suggestions:` list.
  Grep empty hints stay on Grep params (`output_mode="symbols"`, `glob=...`),
  not the separate Glob tool.
- `Grep` MCP schema exposes `output_mode` as an enum
  (`content` | `files_with_matches` | `count` | `symbols`); docs never used
  `mode=files` (that was only an early Search draft).

## 0.3.0 — 2026-07-30

### Breaking

- Replaced the previous retrieval / indexing / review / execution surface with
  eight Cursor-like tools: `Shell`, `GetJobStatus`, `Grep`, `Glob`, `Read`,
  `Write`, `StrReplace`, `Delete`.
- Removed SQLite indexing, FTS, tree-sitter parsers, FastEmbed semantic search,
  hybrid ranking, `build_context`, change sessions, review tools, supervised
  process execution, PowerShell execution, and the `response_detail` projector.
- File tools return plain text (or MCP image content for `Read` of images).
  `Shell` and `GetJobStatus` return structured JSON.
- Optional extras `parsers`, `semantic`, `execution`, and `all` were removed.
  Runtime dependencies are now only `mcp` and `typer`.

### Added

- Path-confined file mutation (`Write`, `StrReplace`, `Delete`).
- Structured `Shell` with optional `shell` selection (`auto` / `powershell` /
  `cmd` / `bash` / `sh`), environment metadata, and opaque `job_id` handoff.
- `GetJobStatus` for waiting on background jobs and reading bounded tails
  without exposing temporary log paths.
- Thin CLI mirroring the file and shell launch tools, plus `code-harness serve`
  for the MCP server.

### Fixed

- On Windows, `Shell` forwards the child process exit code instead of the
  PowerShell success flag.
- Text tools decode windows-1252 when UTF-8 fails, and preserve the original
  encoding on `Write` / `StrReplace` so accented legacy files are not corrupted.
- `Grep` decodes ripgrep's base64 `bytes` JSON field for non-UTF-8 match lines.

## Prior history

Versions before 0.3.0 lived as a local-first indexed code retrieval harness.
See git history for earlier changelog entries.
