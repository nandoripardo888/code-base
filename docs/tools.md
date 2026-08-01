# Tool reference

Most tools return plain text. `Shell` and `GetJobStatus` return structured JSON
so exit codes, job ids, and environment metadata stay unambiguous. Failures come
back as `code: message`, for example
`path_outside_project: Path resolves outside the project root: '../etc'`.

Paths may be relative to the project root or absolute, but must resolve inside
it. Reported paths use forward slashes on every platform.

## Shell

| Parameter | Type | Notes |
|-----------|------|-------|
| `command` | string | required |
| `working_directory` | string | must be inside the project; default is the root |
| `block_until_ms` | int | default `30000`, max `30000`; `0` backgrounds immediately |
| `description` | string | optional label echoed in the response |
| `shell` | string | `auto` (default), `powershell`, `cmd`, `bash`, or `sh` |

Finished within the window:

```json
{
  "status": "completed",
  "job_id": null,
  "pid": 18420,
  "exit_code": 0,
  "elapsed_ms": 1280,
  "output": "BUILD SUCCESS",
  "output_truncated": false,
  "environment": {
    "os": "Windows",
    "shell": "powershell",
    "shell_version": "7.5.2",
    "cwd": "."
  }
}
```

Still running when the wait elapses:

```json
{
  "status": "running",
  "job_id": "job-123",
  "pid": 18420,
  "exit_code": null,
  "elapsed_ms": 30000,
  "last_output": "Compiling...",
  "output_truncated": false,
  "environment": {
    "os": "Windows",
    "shell": "powershell",
    "shell_version": "7.5.2",
    "cwd": "."
  }
}
```

While a command is running, `exit_code` is always `null`. Use `GetJobStatus`
with the returned `job_id` to follow progress. Temporary log paths are never
returned.

`auto` prefers `pwsh` → `powershell` → `cmd` on Windows, and `$SHELL` → `bash`
→ `sh` elsewhere. `CODE_HARNESS_SHELL` overrides that selection. Clear shell
mismatches (for example a Bash heredoc under PowerShell) fail with
`shell_syntax_mismatch`.

## GetJobStatus

| Parameter | Type | Notes |
|-----------|------|-------|
| `job_id` | string | required; opaque id returned by `Shell` |
| `wait_ms` | int | default `0`, max `30000`; how long the query may wait |
| `tail_lines` | int | default `50`, max `500`; trailing lines of output |

```json
{
  "job_id": "job-123",
  "status": "completed",
  "pid": 18420,
  "exit_code": 0,
  "elapsed_ms": 78000,
  "last_output": "BUILD SUCCESS",
  "output_truncated": false
}
```

Allowed statuses: `running`, `completed`, `failed`, `unknown`. An unknown id
returns `status: "unknown"` with `exit_code: null`. There is no cancel API in
this version.

## Grep

| Parameter | Type | Notes |
|-----------|------|-------|
| `pattern` | string | required; ripgrep regex syntax |
| `path` | string | file or directory to search; default is the root |
| `glob` | string or list | filter such as `*.py`, `*.{py,md}`, or `["*.py","*.md"]` |
| `type` | string | ripgrep type name such as `py` or `rust` |
| `output_mode` | string | `content` (default), `files_with_matches`, `count`, `symbols` |
| `case_insensitive` | bool | default `false` |
| `context_before` | int | lines before each match, content mode only |
| `context_after` | int | lines after each match, content mode only |
| `context_lines` | int | lines on both sides; overrides the two above |
| `multiline` | bool | default `false`; lets the pattern span lines |
| `head_limit` | int | maximum matches, or files in the other two modes |
| `offset` | int | skip the first N results |
| `include_all` | bool | default `false`; when false, also skip harness noise |

Match lines are grouped by file: a path heading, then `line:text` for matches
and `line-text` for context (ripgrep heading style). When matches span more
than one file, the first line is a summary such as `3 matches in 2 files`.
With no matches the answer starts with `No matches found.` followed by a short
`Suggestions:` list (case sensitivity, `output_mode="symbols"`, Grep `glob`,
filters, `include_all`).

### `output_mode=symbols`

Finds definitions via language extractors (Python, JS/TS, generic fallback).
Regular content search is unchanged.

- `path` pointing at a **file**: outline that file; `pattern` optionally filters by name
  (empty pattern lists all symbols).
- `path` omitted or a **directory**: requires a non-empty `pattern` (substring match on
  symbol names). `glob`, `type`, `include_all`, `case_insensitive`, paging apply.
- `context_*` and `multiline` are ignored in this mode.

Output groups by file:

```text
2 symbols in 1 file

src/app.py
  10 class App
  24 function run
```

Empty results start with `No symbols found.` plus suggestions.

Results are capped at 1,000 entries even without `head_limit`. When results are
left over, a trailing line tells you the offset to continue from.

Hidden files are searched; `.git` and anything in `.gitignore` are not. By
default a **source-first** layer also skips harness noise (`.code-harness/`,
common caches, `*.err`, plus optional `.code-harnessignore` at the project
root). Pass `include_all=true` to disable that layer. If the filtered search
is empty but ignored paths would match, a short footnote suggests
`include_all=true`. Brace patterns such as `*.{py,md}` are expanded before
ripgrep; malformed braces raise an error instead of returning an empty list.

## Glob

| Parameter | Type | Notes |
|-----------|------|-------|
| `glob_pattern` | string or list | required; `**/` is prepended when missing; braces expand |
| `target_directory` | string | must be inside the project; default is the root |
| `include_all` | bool | default `false`; same source-first layer as Grep |

```text
Result of search in '.' (total 2 files):
- src/util.py
- src/hello.py
```

Sorted by modification time, newest first. An empty result starts with
`No files found matching '...'` and appends generic `Suggestions:`.

## Read

| Parameter | Type | Notes |
|-----------|------|-------|
| `path` | string | required |
| `offset` | int | 1-indexed first line; negative counts from the end |
| `limit` | int | how many lines to return |

Text files come back as `     1|content`, with a trailing note giving the next
offset when lines remain. An empty file answers `File is empty.` Files ending in
`.jpg`, `.jpeg`, `.png`, `.gif`, or `.webp` are returned as MCP image content
instead, so the model can look at them. Files are clipped at 2 MB.

Encoding is UTF-8 first (BOM-aware). If that fails, windows-1252 is used as a
fixed fallback, so accented Brazilian Portuguese sources read correctly.

## Write

| Parameter | Type | Notes |
|-----------|------|-------|
| `path` | string | required |
| `contents` | string | the complete file contents |

Missing parent directories are created. The whole file is replaced, and line
endings are written exactly as given. New files are UTF-8; overwriting an
existing file keeps its detected encoding (UTF-8 or windows-1252).

## StrReplace

| Parameter | Type | Notes |
|-----------|------|-------|
| `path` | string | required |
| `old_string` | string | must exist in the file |
| `new_string` | string | must differ from `old_string` |
| `replace_all` | bool | default `false` |
| `ignore_line_endings` | bool | default `true`; matches LF and CRLF equivalently |
| `expected_occurrences` | int | optional exact occurrence count |
| `expected_sha256` | string | optional stale-file protection |
| `dry_run` | bool | validate without writing |

With `replace_all=false` the old string has to be unique. Matching is tolerant
of LF/CRLF differences by default, while the file's encoding, BOM, and newline
style are preserved. Writes use an atomic replacement.

## ApplyPatch

| Parameter | Type | Notes |
|-----------|------|-------|
| `patch` | string | required unified diff |
| `description` | string | optional observation stored with the transaction; max 500 characters |
| `dry_run` | bool | default `false`; run Git validation without real changes |
| `expected_hashes` | object | optional map of path to expected SHA-256 |

Git is required, but the project does not need a `.git` directory. The patch is
applied first in a temporary UTF-8/LF workspace. Successful results are encoded
back to the original format, committed to the real files, and recorded as
byte-exact history. Binary patches, symlinks, submodules, copies, and renames are
rejected in this version. A successful MCP call returns `review_available`,
`review_url`, and `review_message` at the top level in addition to the rollback
transaction id. The MCP instructions require clients to surface `review_url`
without waiting for a separate `OpenPatchReview` request.

Set `CODE_HARNESS_REVIEW_AUTO_OPEN=true` to open the local browser automatically
after applying a patch. It is disabled by default for headless and remote hosts.

## OpenPatchReview

| Parameter | Type | Notes |
|-----------|------|-------|
| `transaction_id` | string | default `latest`; an id returned by `ApplyPatch` |
| `open_browser` | bool | default `true` |

Starts the review server lazily on an ephemeral `127.0.0.1` port and returns a
one-use browser URL. The page establishes a workspace-scoped session, selects
the requested transaction, and lists every retained `applied` or `rolled_back`
review for the same project, including transactions created by earlier MCP
sessions. It reads only saved before/after snapshots, never arbitrary paths or
the live project. The left-side tree groups each retained review with its changed
files and uses the optional patch description as the primary label. The portal
also provides side-by-side and unified views, collapsed context, change
navigation, light/dark themes, review completion, and safe whole-transaction
rollback.

The URL exchanges its one-use token for an HttpOnly, SameSite session cookie.
POST actions also require CSRF and same-origin checks. The server is stopped by
the owning MCP or CLI session.

## RollbackPatch

| Parameter | Type | Notes |
|-----------|------|-------|
| `transaction_id` | string | required id returned by `ApplyPatch` |
| `force` | bool | default `false`; overwrite later edits only when explicit |

Restores the before snapshots. Without `force`, current files must still match
the hashes produced by the original patch.

History retention and orphan cleanup run internally during server startup; they
are intentionally not exposed as MCP tools.

## Delete

| Parameter | Type | Notes |
|-----------|------|-------|
| `path` | string | required |

Deletes one file. Missing files, directories, and paths outside the project all
produce a `Could not delete ...` message rather than an error, so a redundant
delete never breaks a run.
