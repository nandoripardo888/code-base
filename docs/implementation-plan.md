# Implementation roadmap

Current execution details are tracked in the
[project status](project-status.md). The complete scope, checklists, and
acceptance criteria are maintained in the
[implementation plan](../plano-implementacao.md).

The product is the application toolset; CLI, Python API, MCP, HTTP, and IDE
plugins are adapters.

1. **Bootstrap and lexical core (complete):** layered package, safe discovery and
   reading, Ripgrep literal/regex search, API, CLI, tests, and CI.
2. **Persistence (complete):** SQLite schema and migrations, FTS, hashes,
   incremental/full/verify indexing, index status, and `doctor`.
3. **Structural analysis (complete):** isolated parser workers, circuit breaker,
   Java, Python, and PL/SQL symbols, references, tools, and chunks.
4. **Semantic retrieval (complete):** optional FastEmbed provider, hash cache,
   local cosine index, disk validation, API, and CLI.
5. **Hybrid context (complete):** deterministic classification, parallel candidate
   generation, fused ranking, diversity, disk validation, controlled expansion,
   token budgets, and repository maps.
6. **MCP adapter (complete):** optional SDK, thin handlers, serializers, contract
   tests, and `code-harness mcp serve`.
7. **Hardening (next):** benchmarks, recovery, resource limits, large-project tests, and
   release packaging.

Every phase must preserve lexical operation and safe degradation when its own
optional components are unavailable.
