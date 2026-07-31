# code-harness

A local MCP server with a compact Cursor-like tool set. Eleven tools, no index, no
embeddings, no SQLite.

| Tool | Purpose |
|------|---------|
| `Shell` | Run a shell command; long ones return a `job_id` |
| `GetJobStatus` | Follow a background shell job and its trailing output |
| `Grep` | Regex search over file contents (ripgrep) |
| `Glob` | Find files by glob pattern, newest first |
| `Read` | Read a file as numbered lines, or an image |
| `Write` | Create or overwrite a file atomically |
| `StrReplace` | Guarded substitution, tolerant of LF/CRLF differences |
| `ApplyPatch` | Apply a unified diff through Git in a temporary workspace |
| `OpenPatchReview` | Open a secure local review of a saved patch transaction |
| `RollbackPatch` | Restore byte snapshots saved by `ApplyPatch` |
| `Delete` | Delete a file |

Every path is confined to the project root, so the server cannot read or write
outside the directory it was pointed at.

## Requirements

- Python 3.12 or newer
- [ripgrep](https://github.com/BurntSushi/ripgrep) on `PATH`, needed by `Grep`
  and `Glob`
- Git on `PATH`, needed by `ApplyPatch`; the target directory does **not** need
  to contain a `.git` repository

## Install

```bash
python -m pip install -e ".[dev]"
```

On Windows you can use `scripts/setup.ps1`, and `scripts/setup.sh` elsewhere.

## Running the MCP server

```bash
code-harness serve --project /path/to/project
```

Without `--project` the server uses `CODE_HARNESS_PROJECT`, falling back to the
current directory. The transport is stdio, so point your MCP client at that
command. An entry for a client config looks like this:

```json
{
  "mcpServers": {
    "code-harness": {
      "command": "code-harness",
      "args": ["serve", "--project", "/path/to/project"]
    }
  }
}
```

## CLI

Each tool has a CLI command, mainly for trying things out by hand:

```bash
code-harness grep "TODO" --glob "*.py"
code-harness grep "class " --output-mode files_with_matches
code-harness glob "**/*.ts"
code-harness read src/main.py --offset 1 --limit 40
code-harness write notes.txt "hello"
code-harness str-replace notes.txt "hello" "hi" --expected-occurrences 1
code-harness apply-patch change.patch --dry-run
code-harness apply-patch change.patch
code-harness review latest
code-harness review 20260730T161500-a84f --no-open
code-harness rollback-patch 20260730T161500-a84f
code-harness delete notes.txt
code-harness shell "pytest -q" --block-until-ms 5000 --shell auto
```

## Patch application and rollback

`ApplyPatch` accepts a standard unified diff. It:

1. validates all paths against the project root;
2. copies affected text files to a temporary workspace;
3. converts the temporary copies to UTF-8/LF;
4. runs `git apply --check` and then `git apply` there;
5. restores each file's original encoding, BOM, and line-ending pattern;
6. verifies that the real files did not change during preparation;
7. saves byte-exact before/after snapshots;
8. commits the real-file changes with atomic per-file replacements.

A successful call returns a `transaction_id`. `RollbackPatch` restores the
original bytes and refuses to overwrite later edits unless `force=true`.

Successful MCP patch calls also return a one-use local review URL. The
review page compares the exact before/after snapshots side by side, supports
light and dark themes, marks a transaction reviewed, and can roll back the
whole transaction after revalidating the current file hashes. It binds only to
`127.0.0.1`, loads no CDN resources, and stops with the owning session.

`OpenPatchReview(transaction_id="latest")` reopens the newest applied
transaction. From a terminal, `code-harness review latest` keeps the local page
available until interrupted; add `--no-open` to print the URL without launching
the browser.

The temporary workspace is removed after each call. Persistent history is kept
outside the project by default and does not depend on Git commits, branches,
GitHub, or even the presence of a `.git` directory.

## Automatic history maintenance

History cleanup is internal and is not exposed as MCP tools. When the server
starts, it:

- recovers interrupted patch transactions;
- removes transactions older than the retention policy while keeping recent
  rollback points;
- removes unreferenced snapshot objects;
- removes abandoned temporary workspaces;
- enforces per-workspace and global storage limits.

Defaults can be changed through environment variables:

| Variable | Default |
|----------|---------|
| `CODE_HARNESS_HISTORY_DIR` | platform-local application state directory |
| `CODE_HARNESS_HISTORY_RETENTION_DAYS` | `30` |
| `CODE_HARNESS_HISTORY_KEEP_LAST` | `20` transactions per workspace |
| `CODE_HARNESS_HISTORY_MAX_WORKSPACE_MB` | `250` |
| `CODE_HARNESS_HISTORY_MAX_GLOBAL_MB` | `1024` |
| `CODE_HARNESS_HISTORY_STALE_TEMP_HOURS` | `24` |

## Configuration

| Variable | Effect |
|----------|--------|
| `CODE_HARNESS_PROJECT` | Default project root when `--project` is absent |
| `CODE_HARNESS_RG` | Full path to the ripgrep executable |
| `CODE_HARNESS_SHELL` | Shell used by `Shell` in `auto` mode; defaults to PowerShell on Windows and `$SHELL` elsewhere |

## Background commands

`Shell` waits `block_until_ms` (30 s by default, hard max). If the command is
still running when that elapses, the tool returns `status: "running"` with a
`job_id`. Follow it with `GetJobStatus` (`wait_ms`, `tail_lines`); while the
process runs, `exit_code` stays `null`. Temporary log paths are never returned.
Passing `block_until_ms=0` backgrounds the command immediately, which is useful
for dev servers and watchers.

Scratch logs live outside the project for the server session and are removed on
shutdown.

## Security

Command output is data, not instructions. `Shell` runs with the project root, or
a subdirectory of it, as the working directory, but a command can still do
anything the user can do; path confinement applies to the file tools, not to
programs they start.

Patch paths are validated before Git runs, and binary patches, symlinks,
submodules, renames, and copies are rejected in this version.

## Docs

- [Architecture](docs/architecture.md)
- [Tool reference](docs/tools.md)
