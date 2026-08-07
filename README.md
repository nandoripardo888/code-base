# code-harness

A local MCP server with a compact Cursor-like tool set. Eleven tools, no index,
embeddings, or SQLite. Optional on-demand parsers add syntactic references
without changing the tool count.

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
| `OpenPatchReview` | Deep-link the fixed local review portal |
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
- Optional: `pip install -e ".[parsers]"` for parsed references and richer
  Python, Java, JavaScript, and TypeScript outlines

## Install

```bash
python -m pip install -e ".[dev]"
```

On Windows you can use `scripts/setup.ps1`, and `scripts/setup.sh` elsewhere.

## Running the MCP server

`stdio` remains the default transport, so existing client configurations keep working:

```bash
code-harness serve --project /path/to/project
```

Without `--project` the server uses `CODE_HARNESS_PROJECT`, falling back to the
current directory. An entry for a stdio client config looks like this:

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

To run the same MCP server over Streamable HTTP:

```bash
code-harness serve --transport streamable-http --project /path/to/project
```

The defaults are `127.0.0.1:8000` with the MCP endpoint at `/mcp`. A non-loopback
listen address requires an API key:

```bash
code-harness serve \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key "change-me"
```

HTTP authentication accepts either `Authorization: Bearer <key>` or
`X-Api-Key: <key>`. For a tunnel or public connector, pass `--public-url` so the
public Host and Origin are added to the FastMCP transport-security allowlists.

The HTTP settings can also be supplied through environment variables. Explicit
CLI options take precedence:

```text
CODE_HARNESS_MCP_TRANSPORT=stdio|streamable-http
CODE_HARNESS_MCP_HOST=127.0.0.1
CODE_HARNESS_MCP_PORT=8000
CODE_HARNESS_MCP_PATH=/mcp
CODE_HARNESS_MCP_API_KEY=...
CODE_HARNESS_MCP_PUBLIC_URL=https://example.com/mcp
CODE_HARNESS_MCP_ALLOWED_HOSTS=host1,host2
CODE_HARNESS_MCP_ALLOWED_ORIGINS=https://origin1,https://origin2
CODE_HARNESS_MCP_DISABLE_DNS_REBINDING=0|1
CODE_HARNESS_MCP_NO_API_KEY=0|1
```

`code-harness mcp serve` is an alias with the same transport options.

## CLI

Each tool has a CLI command, mainly for trying things out by hand:

```bash
code-harness grep "TODO" --glob "*.py"
code-harness grep "TODO" --output-mode count --exclude "generated/**"
code-harness grep "class " --output-mode files_with_matches
code-harness grep Helper --output-mode references --glob "*.{py,java,ts}"
code-harness grep Helper --output-mode references --reference-kind implementation
code-harness grep --path src/main.py --output-mode symbols
code-harness glob "**/*.ts"
code-harness read src/main.py --offset 1 --limit 40
code-harness write notes.txt "hello" -m "Create notes" --group-title "Notes flow"
code-harness str-replace notes.txt "hello" "hi" -m "Improve greeting" --group-id GROUP_ID --expected-occurrences 1
code-harness apply-patch change.patch --dry-run
code-harness apply-patch change.patch -m "Apply requested update" --group-title "Patch topic"
code-harness review latest
code-harness review 20260730T161500-a84f --no-open
code-harness rollback-patch 20260730T161500-a84f
code-harness delete notes.txt -m "Remove obsolete notes" --group-id GROUP_ID
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

A successful mutation returns `group_id` and `transaction_id`. When `group_id`
is omitted or blank, the server creates a new group and requires `group_title`.
`description` is the required observation for that update. The first mutation
in a group also requires `group_title`; later `Write`, `StrReplace`,
`ApplyPatch`, and `Delete` calls reuse the returned `group_id` to append to that
group. Omitting it again starts a new group. Dry runs do not create history and
do not require group metadata.

Every successful mutation returns `review_url`. That URL points at the fixed
local portal (`http://127.0.0.1:8765` by default) with optional `group` / `patch`
query params so the UI focuses the new update. The portal is generic: bookmark
`http://127.0.0.1:8765` and browse every retained change for the project. Its
tree is grouped as patch group → update → changed file, so follow-up patches
and direct file edits remain under the same topic.

The portal compares exact before/after snapshots side by side, supports light
and dark themes, marks updates reviewed, rolls back one update, or rolls back an
entire review in reverse order after simulating every snapshot. It binds only to
`127.0.0.1`, loads no CDN resources, starts with the owning session, and stops
when that session ends. Retained transactions remain available the next time the
portal runs for the same project.

Set `CODE_HARNESS_REVIEW_AUTO_OPEN=true` to ask a local installation to open the
browser automatically after a successful patch. The default is disabled for
remote, VM, and headless MCP deployments. Override the listen port with
`CODE_HARNESS_REVIEW_PORT` (default `8765`).

`OpenPatchReview(transaction_id="latest")` deep-links the newest applied
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
- removes complete reviews older than the retention policy while keeping recent
  rollback groups;
- removes unreferenced snapshot objects;
- removes abandoned temporary workspaces;
- enforces per-workspace and global storage limits.

Defaults can be changed through environment variables:

| Variable | Default |
|----------|---------|
| `CODE_HARNESS_HISTORY_DIR` | platform-local application state directory |
| `CODE_HARNESS_HISTORY_RETENTION_DAYS` | `30` |
| `CODE_HARNESS_HISTORY_KEEP_LAST` | `20` reviews per workspace |
| `CODE_HARNESS_HISTORY_MAX_WORKSPACE_MB` | `250` |
| `CODE_HARNESS_HISTORY_MAX_GLOBAL_MB` | `1024` |
| `CODE_HARNESS_HISTORY_STALE_TEMP_HOURS` | `24` |

## Configuration

| Variable | Effect |
|----------|--------|
| `CODE_HARNESS_PROJECT` | Default project root when `--project` is absent |
| `CODE_HARNESS_RG` | Full path to the ripgrep executable |
| `CODE_HARNESS_SHELL` | Shell used by `Shell` in `auto` mode; defaults to PowerShell on Windows and `$SHELL` elsewhere |
| `CODE_HARNESS_REVIEW_PORT` | Loopback port for the review portal (default `8765`) |
| `CODE_HARNESS_REVIEW_AUTO_OPEN` | Open the browser after each successful mutation when `true` |

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
