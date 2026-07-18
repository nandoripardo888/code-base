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
