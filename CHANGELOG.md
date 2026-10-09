# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- `ListPatchReviews` discovers retained review groups after context or process
  changes. It supports state filtering, returns compact counts and the latest
  transaction id, remains isolated by project, and requires OAuth `code.read`.
- `CancelJob` stops an opaque background job and its process tree within the
  selected project, with idempotent terminal responses and OAuth scope
  `code.exec`.
- One MCP process can host multiple named project roots through `ProjectRegistry`;
  project-aware tools accept an optional alias while omitted aliases preserve the
  configured default behavior.
- `ListProjects` and `ProjectInfo` expose safe alias/default context without
  returning filesystem roots.
- Persistent TOML project configuration supports `allowed_project_roots` plus
  transactional `ReloadProjects()` without restarting the MCP endpoint/tunnel.
- Named project sessions share one loopback review portal while keeping separate
  path guards, job registries, histories, and rollback workspaces.
- `Grep` accepts `output_mode=references` for on-demand syntactic references in
  Python, Java, JavaScript, JSX, TypeScript, and TSX through the optional
  `parsers` dependency extra.
- `Grep` and `Glob` accept explicit `exclude` glob patterns; these remain active
  even with `include_all=true`.
- Reference searches accept include/exclude kind filters and identify Java and
  TypeScript interface implementations separately.

### Changed

- `GetJobStatus` accepts an optional opaque cursor for lossless incremental log
  reads of up to 64 KiB without repeating earlier output. Its existing trailing
  output response remains unchanged when the cursor is omitted.
- Shell registries now enforce configurable per-project concurrency, completed
  job count, and retention age limits. Pruning removes the associated private
  log and never removes running jobs.
- `Read` now scans large text incrementally, reports its 2,000,000-byte output
  limit, resolves negative offsets from the real end, rejects likely binary
  content, and limits recognized images to 4 MiB before loading them completely.
- `Glob` now selects the globally newest 1,000 matches and uses the normalized
  path as a deterministic tie-breaker while keeping MCP candidate memory bounded.
- Clarified the security contract: direct file-tool paths are project-confined,
  while `Shell` intentionally retains the host user's permissions and is governed
  by authentication, the `code.exec` scope, and the tool allowlist.
- Legacy single-project startup remains compatible, while `serve` / `mcp serve`
  also accept repeatable `alias=path`, `--default-project`, or `--project-config`.
- Background jobs, patch history, rollback, and review routing are isolated per
  selected project; structured execution/mutation results include the resolved
  project alias.
- `Grep.pattern` may be omitted for a `symbols` outline scoped to one file.
- `Grep` `count` mode now reports total matches, total files, and a deterministic
  per-file ranking before paginating the file list.
- Parsed symbol outlines prefer syntactic declarations over textual fallbacks,
  eliminating duplicate methods and false exception-constructor functions.
- Chained method calls below `new` expressions are classified as calls rather
  than instantiations.

## 0.5.0 — 2026-08-01

### Breaking

- Persistent `Write`, `StrReplace`, `ApplyPatch`, and `Delete` calls now require
  an update `description` plus either `group_title` when creating a group or the
  returned `group_id` for later related changes.

### Added

- Patch grouping is explicit and independent of MCP sessions. The server creates
  a `group_id` when it is omitted and returns it for intentional reuse.
- Patch groups are first-class persisted entities with the hierarchy group → update →
  file. All mutating tools save byte-exact snapshots and return a review URL.
- The review portal completes and rolls back individual updates and can safely
  roll back a whole review after a reverse simulation detects external or
  interleaved changes.
- Existing transaction-only history is projected as one legacy review per
  transaction without rewriting saved snapshots.

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
