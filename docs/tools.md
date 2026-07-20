# Tools

The current application tools are independent from their interfaces.

| Tool | Purpose | Index required |
|---|---|---:|
| `list_files` | List allowed source files | No |
| `search_files` | Match names and relative paths | No |
| `search_text` | Validated FTS candidates plus exact Ripgrep fallback | No |
| `search_regex` | Regular-expression search using Ripgrep | No |
| `read_file` | Read a guarded source file | No |
| `read_range` | Read an inclusive line interval | No |
| `index_project` | Run incremental, full, or verify indexing | Creates it |
| `get_index_status` | Return schema, counts, state, and last run | No |
| `doctor` | Check runtime, project, Ripgrep, SQLite, cache, and optional deep inference | No |
| `prepare_semantic_model` | Download, cache, load, and probe the embedding model | No |
| `get_file_outline` | Return validated symbols for one file | Yes |
| `find_symbol` | Find symbols by name or qualified name | Yes |
| `find_definition` | Find exact symbol definitions | Yes |
| `find_references` | Combine structural and lexical references | No |
| `semantic_search` | Rank validated chunks by semantic similarity | Yes |
| `search_code` | Fuse lexical, structural, path, reference, and optional semantic evidence | No |
| `build_context` | Select and expand validated snippets within an estimated token budget | No |
| `get_repository_map` | Return a current file tree enriched with validated indexed symbols | No |

Search and read limits are validated by request DTOs. Results use `ToolResult`
with elapsed time, truncation state, warnings, and optional index state.

Structural tools return `StructuralSearchResult` objects containing the symbol
or reference, current source content, and current hash. Semantic results reuse
`SearchHit` with `match_type=semantic` and a raw cosine score. Hybrid results use
`HybridSearchHit` with per-strategy `SearchEvidence`; scores are fused
deterministically and results are diversified by file and directory.

`build_context` expands only known symbol parents and direct references. Its
token count is a conservative local estimate (`ceil(UTF-8 bytes / 3)`), not a
model-specific tokenizer result. `get_repository_map` still returns the current
file tree when the structural index is unavailable, with a warning and no symbol
enrichment.

Use `doctor(deep=True)` in Python or `doctor --deep` in the CLI for actual model
loading and inference. Use `prepare_semantic_model()` or `models prepare` before
the first semantic index so network and certificate problems are reported
before repository processing starts.
