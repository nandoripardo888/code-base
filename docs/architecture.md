# Architecture

`code-harness` uses inward-pointing dependencies:

```text
interfaces -> application -> domain
                         ^
                         |
                  infrastructure
```

- `domain` owns immutable models, errors, enums, and protocols. It imports no
  project layer.
- `application` owns use-case tools. It depends only on the domain contracts.
- `infrastructure` implements those contracts for the local filesystem and
  Ripgrep.
- `interfaces` translates Python and CLI calls into application DTOs and renders
  structured results.
- `bootstrap` performs manual dependency composition and configuration.

MCP is planned as an optional interface adapter. It will not own search,
filesystem, ranking, indexing, or context-building behavior. Removing a future
`interfaces/mcp` directory must leave the Python API and CLI operational.

## Phase-one request flow

```text
CLI / CodeHarness
       |
       v
Application tool
       |
       v
Domain protocol
       |
       v
Filesystem / Ripgrep adapter
```

Search matches are re-read through the guarded source reader. The returned
snippet therefore contains the current file content and SHA-256 hash, not merely
the bytes emitted by the search process.

## Structural-analysis flow

```text
IndexCoordinator
       |
       v
StructuralAnalyzer registry
       |
       v
NativeParserSupervisor -- JSON --> disposable worker subprocess
                                      |-- Tree-sitter Java/Python when installed
                                      |-- dedicated compatible extractors
                                      `-- symbols, references, chunk boundaries
       |
       v
Chunk builder --> atomic SQLite update
```

The worker is the only module allowed to load Tree-sitter or grammar-native
state. Timeout, invalid output, or a dead worker is converted to a typed parser
error. The coordinator then persists a textual fallback chunk, records the
failure, and leaves lexical search operational.

Structural queries treat SQLite as a candidate locator. They re-read the current
file and compare SHA-256 before returning source ranges, skipping stale records.

## Semantic-retrieval flow

```text
Persisted/new chunks -> hash cache -> optional EmbeddingProvider
                              |                 |
                              `---- SQLite <---'
                                      |
query -> query embedding -> cosine VectorIndex -> current-file validation
```

The domain owns the provider and vector-index contracts. FastEmbed is imported
only by a disposable embedding worker and is loaded only when semantic indexing
or search is requested. The supervisor contains timeouts, invalid output, and
native-process crashes. SQLite stores the derived vectors, while the source file
remains authoritative for returned paths, ranges, content, and hashes. Provider
failures become semantic warnings and never remove lexical or structural data.

## Hybrid-search and context flow

```text
query -> deterministic classifier
            |-- lexical / FTS
            |-- symbols and references
            |-- repository paths
            `-- optional semantic search
                         |
              weighted RRF + boosts
                         |
             deduplication + diversity
                         |
               current-file validation
                         |
             search result / context builder
                              |-- parent symbols
                              |-- direct references
                              `-- estimated token budget
```

Each candidate strategy runs independently and optional failures become
warnings. Hybrid evidence records the source rank, normalized score, and fused
contribution. Context expansion is bounded, cycle-safe, and uses only relations
persisted by structural analysis. The repository map is assembled from the
current file catalog and attaches symbols only after hash validation.
