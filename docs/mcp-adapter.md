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

Successful calls default to `response_detail=compact`, shared with CLI JSON:

```json
{
  "data": []
}
```

Every tool accepts `response_detail=minimal|compact|detailed|debug|full`.
An explicit value overrides `CODE_HARNESS_RESPONSE_DETAIL`; the fallback is
`compact`.

- `minimal` returns only the essential tool data.
- `compact` adds useful locations, types, scores, and truncation markers.
- `detailed` adds consumer-facing metadata without internal IDs or hashes.
- `debug` adds a `diagnostics` object with timings, index state, strategies,
  evidence, and other internal metadata.
- `full` returns the rich legacy envelope.

The four normal profiles limit the serialized `data` section to 30,000
characters. If the adapter removes tail items or clips a single read, it adds
`truncated: true` and a structured `truncation` object. Its `reasons` may contain
`result_limit`, `snippet_line_limit`, `snippet_char_limit`, `token_budget`,
`candidate_limit`, `file_limit`, `expansion_limit`, or `response_budget`.
`omitted_results` is included only when its value is known. Deduplication alone
does not mark a response as truncated. `full` bypasses the aggregate budget.
Warnings may be structured objects with `code`, `message`, `recoverable`,
`capability`, and `remediation`; non-empty warnings are always preserved.

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
Unexpected failures use `code=internal_error` with a correlation `error_id` and
tool name. The client never receives the original exception or traceback; the
server log retains both under that ID.

## Degradation notes

- `search_regex` requires Ripgrep. Configure `CODE_HARNESS_RG` or install `rg`.
  There is no Python regex fallback.
- `find_references` prefers the structural index and degrades to Ripgrep when
  available; without Ripgrep it still returns validated structural references.
  Confirmed lexical comments are ranked last as `comment_textual` and can be
  removed with `include_comments=false`.
- `get_file_outline` / `find_symbol` default to compact responses without symbol
  bodies (`include_content=false`, `response_format=compact`).
- `list_files` returns a paginated page (`items`, `next_cursor`, …).
- `read_file` / `read_range` return `SourceRead` with truncation metadata.
- Numbered compact reads return `lines` without duplicating the same source in
  `content`; `full` retains the legacy representation.
- `search_code` defaults to query-focused snippets of at most 40 lines and
  6,000 characters. These limits apply in every response profile, including
  `full`, and can be increased explicitly with the search parameters.
- Hybrid compact results include actual `matched_terms` and
  `scope=anchor|fallback`. Scores are absolute rather than normalized to the
  best result.
- `get_repository_map` defaults to `mode=summary` (no symbols); use `detailed`
  for symbol enrichment. Directory filters normalize `/`, `\`, `./`, repeated
  separators, and trailing separators.
- `get_index_status` exposes `capabilities`, `index_state`, `service_state`,
  `service_version`, optional `build_commit`, `service_started_at`, and
  `service_instance_id`.
  Index readiness and embedding/service health are reported separately.
- Semantic failures are cached in-process until configuration changes or
  `doctor --deep` invalidates them.
