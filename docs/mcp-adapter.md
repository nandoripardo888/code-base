# MCP adapter

The MCP adapter is an optional thin interface over the same application tools
used by the CLI and Python API. Handlers validate protocol input, construct
application DTOs, execute tools, serialize structured results, and map typed
errors. Direct filesystem, Ripgrep, SQL, parsing, embedding, ranking, and
context logic remain outside the adapter.

## Install

```powershell
python -m pip install -e ".[mcp]"
```

The `dev` extra also installs the MCP SDK so contract tests can run without a
separate optional install.

## Serve

```powershell
code-harness --project "C:\projetos\sample_project" mcp serve
```

The project root is resolved once at startup from `--project`,
`CODE_HARNESS_PROJECT`, or the active project registry. Clients cannot change
the root after the server starts.

## Exposed tools

By default:

- `list_files`
- `search_files`
- `search_text`
- `search_regex`
- `read_file`
- `read_range`
- `get_file_outline`
- `find_symbol`
- `find_references`
- `semantic_search`
- `search_code`
- `build_context`
- `get_repository_map`
- `get_index_status`

`index_project` is registered only when
`CODE_HARNESS_MCP_EXPOSE_INDEX=1` (or `true`/`on`/`yes`). Administrative tools
such as `doctor` stay out of the MCP surface.

## Result envelope

Successful calls return the shared structured payload used by CLI JSON output:

```json
{
  "data": {},
  "elapsed_ms": 12,
  "truncated": false,
  "warnings": [],
  "index_state": "ready",
  "strategies": []
}
```

Warnings may be plain strings or structured objects with `code`, `message`,
`recoverable`, `capability`, and `remediation`. Empty `strategies` are omitted.

Typed failures return:

```json
{
  "error": {
    "code": "path_outside_project",
    "message": "...",
    "details": {},
    "recoverable": false
  }
}
```

Recoverable capability errors such as `ripgrep_unavailable` and
`embedding_unavailable` also include `capability` and `remediation`.

## Degradation notes

- `search_regex` requires Ripgrep. Configure `CODE_HARNESS_RG` or install `rg`.
  There is no Python regex fallback.
- `find_references` prefers the structural index and degrades to Ripgrep when
  available; without Ripgrep it still returns validated structural references.
- `get_file_outline` / `find_symbol` default to compact responses without symbol
  bodies (`include_content=false`, `response_format=compact`).
- `list_files` returns a paginated page (`items`, `next_cursor`, …).
- `read_file` / `read_range` return `SourceRead` with truncation metadata.
- `get_repository_map` defaults to `mode=summary` (no symbols); use `detailed`
  for symbol enrichment.
- `get_index_status` exposes `capabilities`, `index_state`, and `service_state`.
  Index readiness and embedding/service health are reported separately.
- Semantic failures are cached in-process until configuration changes or
  `doctor --deep` invalidates them.
