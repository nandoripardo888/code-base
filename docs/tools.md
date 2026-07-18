# Tools

The current application tools are independent from their interfaces.

| Tool | Purpose | Index required |
|---|---|---:|
| `list_files` | List allowed source files | No |
| `search_files` | Match names and relative paths | No |
| `search_text` | Literal content search using Ripgrep | No |
| `search_regex` | Regular-expression search using Ripgrep | No |
| `read_file` | Read a guarded source file | No |
| `read_range` | Read an inclusive line interval | No |

Search and read limits are validated by request DTOs. Results use `ToolResult`
with elapsed time, truncation state, warnings, and optional index state.

Planned tools—indexing, outlines, symbols, references, semantic and hybrid
search, repository maps, and context construction—will follow the same contract.
