# Semantic search

Semantic search is an optional local capability. Install `code-harness[semantic]`,
set `CODE_HARNESS_SEMANTIC=1`, and run `code-harness index` before using
`code-harness search semantic` or `CodeHarness.semantic_search`.

Prepare the model explicitly before the first index:

```powershell
$env:CODE_HARNESS_SEMANTIC="1"
code-harness models prepare
code-harness doctor --deep
```

`models prepare` downloads the configured model, stores it in the persistent
model cache, loads it in an isolated worker, and validates a real query plus a
multi-batch document probe containing Unicode and an empty document.
The default cache is `%LOCALAPPDATA%\code-harness\models` on Windows and
`~/.cache/code-harness/models` on Linux. Use `CODE_HARNESS_MODEL_CACHE` to
override it.

The local provider uses FastEmbed and defaults to the multilingual 384-dimension
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` model. Model files
are downloaded and cached locally on first use; source code is never sent to an
embedding service.

Chunks longer than 1,500 characters are split into overlapping, line-aware
windows. Window vectors are averaged and L2-normalized into one vector per
structural chunk. The strategy identifier, provider version, model, dimensions,
and content hash form the cache identity, so incompatible vectors never mix.

SQLite stores float32 vectors and links them to live chunks. Search loads only
the active model, computes cosine similarity in memory, and then re-reads the
source file and validates its hash before returning content. Stale or removed
results are skipped with warnings.

Provider failures finish indexing as `ready_with_warnings`; FTS, Ripgrep,
structural tools, and direct reads remain available. A semantic request made
while disabled or unprepared raises `embedding_unavailable`.

Model loading and inference run in disposable subprocesses. A timeout, native
runtime crash, or invalid worker response cannot terminate the CLI or leave the
parent process unable to record a warning. Index runs record their owner PID;
status and subsequent indexing recover abandoned `indexing` runs after that
process exits.

The worker protocol uses encoding-neutral JSON escapes, so Windows console code
pages cannot corrupt source text. This is transport handling only: indexing
reads source encodings and never rewrites project files. JSON and JSONL CLI
output use the same console-safe representation; JSON consumers recover the
original Unicode values normally.

The CLI and worker use the native operating-system certificate store through
`truststore`. Set `CODE_HARNESS_SYSTEM_TRUST=0` to opt out, or
`CODE_HARNESS_CA_BUNDLE=/path/to/company.pem` to provide an explicit CA bundle.
TLS verification is never disabled.

Hybrid ranking remains part of phase five.
