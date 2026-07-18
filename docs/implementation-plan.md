# Implementation roadmap

The product is the application toolset; CLI, Python API, MCP, HTTP, and IDE
plugins are adapters.

1. **Bootstrap and lexical core (current):** layered package, safe discovery and
   reading, Ripgrep literal/regex search, API, CLI, tests, and CI.
2. **Persistence:** SQLite schema and migrations, FTS, hashes, incremental index,
   index status, `doctor`, and repair.
3. **Structural analysis:** isolated parser workers, circuit breaker, Java,
   Python, and PL/SQL symbols and chunks.
4. **Semantic retrieval:** optional embeddings, cache, local vector index, and
   semantic search.
5. **Hybrid context:** deterministic classification, fused ranking, diversity,
   disk validation, controlled expansion, and token budgets.
6. **MCP adapter:** optional SDK, thin handlers, serializers, and contract tests.
7. **Hardening:** benchmarks, recovery, resource limits, large-project tests, and
   release packaging.

Every phase must preserve lexical operation and safe degradation when its own
optional components are unavailable.
