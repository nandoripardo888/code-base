# Tools

The current application tools are independent from their interfaces.

| Tool | Purpose | Index required |
|---|---|---:|
| `list_files` | List allowed source files with stable pagination | No |
| `search_files` | Match names and relative paths with match evidence | No |
| `search_text` | Validated FTS candidates plus exact Ripgrep fallback | No |
| `search_regex` | Regular-expression search using Ripgrep only | No |
| `read_file` | Read a guarded source file with truncation metadata | No |
| `read_range` | Read an inclusive line interval with truncation metadata | No |
| `index_project` | Run incremental, full, or verify indexing | Creates it |
| `get_index_status` | Return schema, counts, index/service state, and capabilities | No |
| `doctor` | Check runtime, project, Ripgrep, SQLite, cache, and optional deep inference | No |
| `prepare_semantic_model` | Download, cache, load, and probe the embedding model | No |
| `get_file_outline` | Return validated symbols for one file | Yes |
| `find_symbol` | Find symbols by name or qualified name | Yes |
| `find_definition` | Find exact symbol definitions | Yes |
| `find_references` | Prefer structural references; Ripgrep is optional | No |
| `semantic_search` | Rank validated chunks by semantic similarity | Yes |
| `search_code` | Fuse lexical, structural, path, reference, and optional semantic evidence | No |
| `build_context` | Select and expand validated snippets within an estimated token budget | No |
| `get_repository_map` | Return a current file tree; symbols only in detailed mode | No |

Search and read limits are validated by request DTOs. Results use `ToolResult`
with elapsed time, truncation state, warnings, optional index state, and
optional strategy outcomes.

Warnings may be plain strings or structured `ToolWarning` objects (`code`,
`message`, `recoverable`, `capability`, `remediation`). Use
`warning_message()` when consuming them.

## Contracts that matter for agents

### Pagination

`list_files` returns a `FileListingPage` with `items`, `next_cursor`,
`total_count`, `page_size`, and `has_more`. Pass `cursor` / `page_size` for the
next page. A stale cursor raises `cursor_stale`.

`get_repository_map` accepts `mode=summary|files|detailed` (default `summary`).
Symbols are included only in `detailed`. Summary and files modes stay light.

### Compact structural responses

`get_file_outline` and `find_symbol` default to `include_content=false` and
`response_format=compact`. Symbol bodies are omitted (`content=None`,
`content_included=false`). Request `include_content=true` or
`response_format=full` when bodies are required.

Symbols expose both `display_signature` (human-oriented) and
`canonical_signature` (normalized, multiline-aware). Schema migration v5 and
parser supervisor version 3 cover the signature fields.

### Reads and truncation

`read_file` and `read_range` return `SourceRead` with the snippet plus
`TruncationInfo` (`truncated`, `reason`, `next_start_line`, character/line
limits). Truncation prefers line boundaries. `include_line_numbers` controls
prefixed line numbers in the returned text.

### References and regex

`find_references` is structural-first. Ripgrep complements when available; if
Ripgrep is missing or times out, validated structural references are still
returned with structured warnings. Textual hits may be marked
`unknown_textual` until validated against the current file.

`search_regex` requires Ripgrep. There is no Python regex fallback. Configure
`CODE_HARNESS_RG` or ensure `rg` is on `PATH`. Doctor reports discovery details.

### Index vs service health

`get_index_status` separates:

- `index_state` — persistence readiness (`ready`, `ready_with_warnings`, …);
- `service_state` — runtime services such as embeddings;
- `capabilities` — per-capability `CapabilityStatus` (`ready`, `degraded`,
  `unavailable`, `disabled`, `unknown`).

Semantic probe failures are cached in-process until configuration changes or
`doctor --deep` invalidates the cache.

### Hybrid search and context

Structural tools return `StructuralSearchResult` objects containing the symbol
or reference, optional current source content, and current hash. Semantic
results reuse `SearchHit` with `match_type=semantic` and a raw cosine score.
Hybrid results use `HybridSearchHit` with per-strategy `SearchEvidence`; scores
are fused deterministically and results are diversified by file and directory.

`search_code` may expand camelCase / snake_case terms for conceptual and mixed
queries. Expansion is lexical only (no translation). Match evidence includes
precise match types and character spans where available.

`build_context` expands only known symbol parents and direct references. Its
token count is a conservative local estimate (`ceil(UTF-8 bytes / 3)`), not a
model-specific tokenizer result. Omission reasons are typed and mutually
exclusive (`results_truncated`, `snippet_truncated`, `budget_exhausted`,
`expansion_limited`). Query-oriented windows prefer ranges that contain the
query terms.

`get_repository_map` still returns the current file tree when the structural
index is unavailable, with a warning and no symbol enrichment.

Use `doctor(deep=True)` in Python or `doctor --deep` in the CLI for actual model
loading and inference. Use `prepare_semantic_model()` or `models prepare` before
the first semantic index so network and certificate problems are reported
before repository processing starts.
