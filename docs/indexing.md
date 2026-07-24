# Indexing

Phase two provides SQLite as the index source of truth, explicit migrations,
SHA-256 change detection, FTS5, index-run history, integrity status, and
incremental updates. The default database is `.code-harness/index.db` inside the
project and that directory is excluded from discovery and Ripgrep.

For Git worktrees, discovery delegates to `git ls-files --cached --others
--exclude-standard`. This honors tracked files, root and nested `.gitignore`
files, `.git/info/exclude`, and global Git excludes with Git's own precedence
rules. If Git is unavailable or the directory is not a worktree, the local
scanner applies root and nested `.gitignore` files with the same scoped-rule
model. Built-in safety exclusions still remove caches, secrets, and known
binary artifacts in both modes.

`incremental` skips reads when persisted size and modification time are
unchanged, then uses SHA-256 before reprocessing metadata candidates. `full`
rehashes every discovered file but only rewrites changed content. `verify`
reports differences without applying them.

## Partial indexing

`--include` / `--exclude` (and the matching API/MCP `include_globs` /
`exclude_globs`) limit a single run to a temporary path scope. They are not
project ignore rules: `.gitignore` and the built-in safety exclusions still
apply during discovery.

Semantics:

- with no filters, behavior is unchanged: missing files are removed from the
  index and `.gitignore` changes are reflected globally;
- discovery lists only files that match the include/exclude filters;
- removals consider only stored paths that belong to that same scope;
- paths outside the scope stay untouched, even if they disappeared on disk;
- an explicitly excluded path is treated as out of scope, so its current index
  entry is preserved for that run;
- `verify` with a partial scope still reports differences without writing file
  updates;
- the report sets `partial=true` and includes `include_globs`, `exclude_globs`,
  `scoped_discovered_files`, and `preserved_out_of_scope_files` so a scoped run
  is not mistaken for a full project validation.

```text
code-harness index --include src --exclude generated
code-harness index --mode verify --include "src/**/*.java"
```

Phase three extends each changed-file update with structural analysis and
syntax-aware chunking. Java and Python use Tree-sitter when the optional parser
extra is installed; compatible dedicated extractors remain available in the
isolated worker. PL/SQL uses a dedicated boundary-aware extractor.

The structural schema stores symbols, references, chunks, parser metadata, and
parser failures. A content, parser, or chunking-version change invalidates the
affected file. A truly unchanged incremental run performs no source reads or
parses.

Analyzer output is validated before persistence. Duplicate identifiers or
locations belonging to another file cause only that file to fall back to safe
textual chunks. Source/FTS updates, structural data, and semantic links are
committed in separate stages, so a failure in an optional stage cannot roll
back an already valid lexical index.

Phase four adds a versioned embedding cache and chunk-to-embedding links. When
semantic search is enabled, indexing embeds only chunks missing the active
provider/model/strategy identity. Duplicate content and renamed chunks reuse
the same cached vector. A model or windowing change re-embeds stored chunk
content without reading or parsing unchanged source files.

`verify` reports missing semantic records without generating them. Provider or
model failures add a warning and leave the lexical and structural transaction
usable. Removed chunks lose their vector links through foreign-key cascades;
cached vectors remain available for later hash reuse.

Each index run records its owner PID. `status` and the next indexing attempt
mark an unfinished run as interrupted only after that process no longer exists,
preventing a native worker crash from leaving the project permanently in
`indexing`.

```text
code-harness index --mode incremental
code-harness index --mode full
code-harness index --mode verify
code-harness index --include src --exclude generated
code-harness status
code-harness doctor
code-harness doctor --deep
code-harness models prepare
```

FTS results are candidates only. Every returned hit is validated against the
current file. Direct Ripgrep search and source reading remain available when the
index is absent or unhealthy.

Structural results follow the same rule: the current file is re-read and its
SHA-256 hash must match the indexed record. Stale results are skipped with a
warning. Parser failures store safe textual chunks, keep FTS current, and finish
the run as `ready_with_warnings`.
