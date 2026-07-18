# ADR 0004: MCP as an adapter

Status: accepted

MCP declares schemas, validates protocol input, invokes application tools,
serializes results, and maps errors. It contains no retrieval or indexing logic
and remains an optional package extra.
