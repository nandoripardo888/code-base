# ADR 0002: SQLite as the index source of truth

Status: accepted for phase two

SQLite will own durable file, chunk, symbol, run, and embedding metadata. FTS and
vector structures are derived retrieval indexes. Direct disk reads remain the
final authority for returned source content.
