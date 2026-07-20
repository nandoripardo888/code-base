# Troubleshooting

## `ripgrep_unavailable`

Install Ripgrep and ensure `rg --version` succeeds, or configure the executable
through `CODE_HARNESS_RG`. If the configured override does not resolve to an
executable, `code-harness` falls back to the `rg` available through `PATH`.

## `project_not_found`

Run `code-harness init <path>`, pass `--project <path>`, or set
`CODE_HARNESS_PROJECT`.

## `path_outside_project`

Only relative paths within the selected project are accepted. Symlinks resolving
outside the project are rejected.

## Empty search results

Check `.gitignore`, default ignored directories, include/exclude globs, case
sensitivity, and the selected active project. Use `--output json` to inspect
warnings and metadata.

In a Git worktree, `git check-ignore -v path/to/file` shows the exact rule used
by both Git and project discovery. Tracked files remain discoverable even when
they match a later ignore rule. Outside a Git worktree, root and nested
`.gitignore` files are still applied by the fallback scanner.

## `index_not_ready`

Run `code-harness init <path>` to create the database, then run
`code-harness index`. Lexical search remains available before initialization.

## `index_corrupted`

Run `code-harness doctor` and inspect the SQLite integrity and schema checks.
Direct lexical search continues through Ripgrep while indexed search is
unavailable. A dedicated repair command remains planned; preserve the corrupted
database for diagnosis before recreating the local index.

## Parser warnings

Install the optional native grammars with `python -m pip install -e
".[parsers]"`. Use `code-harness doctor` to check the isolated worker. Parsing
can be disabled with `CODE_HARNESS_PARSERS=0`; indexing then creates textual
chunks and keeps all lexical capabilities.

`parser_timeout`, `parser_crash`, and `parser_circuit_open` affect only the
structural result for the current file. The index run records the failure,
finishes as `ready_with_warnings`, and preserves Ripgrep, FTS, and direct reads.
Changing the file hash allows the payload to be attempted again.

Duplicate or otherwise invalid parser identifiers also fall back per file and
are reported with the affected path and identifier. They do not abort the
project index or prevent semantic links for other valid chunks.

Use `CODE_HARNESS_PARSER_TIMEOUT_SECONDS` to adjust the per-file timeout.

## `embedding_unavailable`

Install the optional dependency with `python -m pip install -e ".[semantic]"`,
set `CODE_HARNESS_SEMANTIC=1`, and rerun `code-harness index`. The first enabled
run downloads the configured local model, so it needs network access unless the
model is already cached.

Use `code-harness doctor` to verify the provider configuration without running
inference, or `code-harness doctor --deep` to download/load the model and run a
real inference probe. Run `code-harness models prepare` before indexing. Check
`CODE_HARNESS_EMBEDDING_PROVIDER` and
`CODE_HARNESS_EMBEDDING_MODEL` when a custom value is rejected. A failed model
download or inference produces `ready_with_warnings`; lexical and structural
search remain operational and a later incremental run retries missing vectors.

## TLS and native runtime errors

The semantic CLI uses the system trust store by default. For an enterprise CA
that is not installed in the operating system, set `CODE_HARNESS_CA_BUNDLE` to
a PEM bundle maintained by your organization. Do not disable TLS verification.
Standard `HTTPS_PROXY` and `HTTP_PROXY` environment variables are inherited by
the model worker when the network requires an explicit proxy.

`CERTIFICATE_VERIFY_FAILED` means the configured trust store does not recognize
the HTTPS certificate chain. `OPENSSL_Applink` indicates an incompatible
embedded Python/OpenSSL runtime on Windows; recreate the virtual environment
with an official Python 3.12+ installation. The embedding worker contains the
crash and returns `embedding_unavailable` to the parent CLI.

Use `CODE_HARNESS_EMBEDDING_TIMEOUT_SECONDS` for slow first downloads and
`CODE_HARNESS_MODEL_CACHE` for a persistent or pre-provisioned model directory.

The application does not require source files to be rewritten as UTF-8. Worker
requests and CLI JSON output are encoding-neutral even when Windows uses a
legacy console code page. If an individual source encoding cannot be decoded,
the file is skipped with `unsupported_encoding` while the remaining project is
indexed.
