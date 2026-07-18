# MCP adapter roadmap

MCP will be a thin optional interface added after the application tools are
stable. Each handler will validate protocol input, construct an application DTO,
execute a tool, serialize the structured result, and map typed errors. Direct
filesystem, Ripgrep, SQL, parsing, embedding, ranking, and context logic are
forbidden in the adapter.
