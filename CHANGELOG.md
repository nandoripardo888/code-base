# Changelog

All notable changes to this project will be documented in this file.

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
