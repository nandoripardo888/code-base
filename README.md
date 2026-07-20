# code-harness

`code-harness` is a local-first Python library and CLI for retrieving verifiable
context from source repositories. The current release combines safe filesystem
access, direct lexical search, an incremental SQLite index, and isolated
structural analysis. Semantic search is available as an optional local extra;
deterministic hybrid ranking and budgeted context construction work with or
without that extra. MCP remains planned.

## Current capabilities

- discover text files while honoring safe defaults and `.gitignore`;
- find files by name or path;
- search literal text and regular expressions with Ripgrep;
- read complete files or line ranges;
- reject paths and symlinks that escape the project root;
- return immutable, structured Python results with paths, lines, hashes, scores,
  timings, and warnings;
- expose the same application tools through Python and the CLI.
- initialize and migrate a local SQLite index;
- incrementally index only new, changed, or removed files;
- use validated SQLite FTS candidates while preserving the Ripgrep fallback;
- report index state and run local diagnostics.
- honor Git's tracked/untracked view, nested `.gitignore` files, repository
  excludes, and global excludes during discovery;
- parse Java and Python with optional Tree-sitter grammars in an isolated worker;
- extract PL/SQL packages, procedures, functions, triggers, and cursors;
- persist symbols, references, and syntax-aware chunks incrementally;
- query outlines, symbols, definitions, and structural/textual references;
- preserve lexical indexing when a parser times out, crashes, or is disabled.
- generate optional local multilingual embeddings with FastEmbed;
- cache embeddings by model, strategy, and content hash in SQLite;
- search current, hash-validated chunks by cosine similarity.
- classify exact, conceptual, and mixed queries without an LLM;
- fuse lexical, structural, path, reference, and optional semantic evidence;
- build current-file-validated context within a conservative token estimate;
- return a structured repository tree enriched with validated symbols.

No repository code is executed. The analyzed repository is treated as read-only.

## Requirements

- Python 3.12+
- [Ripgrep](https://github.com/BurntSushi/ripgrep) available as `rg`

## Install and use

```powershell
git clone <repository-url>
cd code-harness

python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
# Optional native Tree-sitter grammars:
python -m pip install -e ".[parsers]"
# Optional local semantic search (downloads the configured model on first use):
python -m pip install -e ".[semantic]"

code-harness init "C:\projetos\sample_project"
code-harness index --mode incremental
code-harness status
code-harness doctor
code-harness files list
code-harness files search "AgendaService"
code-harness search text "AgendaService"
code-harness search regex "public\s+void\s+setFilter"
code-harness outline "src\AgendaService.java"
code-harness search symbol "AgendaService"
code-harness definition "montarAgendaConsultor"
code-harness references "validarAgenda"
code-harness search semantic "como a agenda distribui serviços"
code-harness search hybrid "como AgendaService distribui serviços"
code-harness context "como a agenda do consultor funciona?" --max-tokens 12000
code-harness map
code-harness read "src\AgendaService.java" --lines 100:180
```

On a new Windows development machine, the assisted setup validates Python and
Ripgrep, installs the tested semantic dependency set, downloads the model, and
runs a real inference probe:

```powershell
.\scripts\setup.ps1 -Semantic -Parsers
```

On Linux, use `CODE_HARNESS_SETUP_SEMANTIC=1
CODE_HARNESS_SETUP_PARSERS=1 sh ./scripts/setup.sh`.

Linux activation uses `source .venv/bin/activate`. A project can also be selected
without changing the active registration:

```bash
code-harness --project /work/repository search text "needle"
CODE_HARNESS_PROJECT=/work/repository code-harness files list
```

Use `--output json`, `jsonl`, `text`, `table`, or `llm` before the subcommand.
Use `python -m code_harness` interchangeably with `code-harness`.

Semantic search is disabled by default. Enable it before indexing:

```powershell
$env:CODE_HARNESS_SEMANTIC="1"
code-harness models prepare
code-harness doctor --deep
code-harness index --mode incremental
code-harness search semantic "validação para encerrar uma OS"
```

Indexing never rewrites project files. Source encoding is detected during
reading, while isolated parser/embedding workers use an encoding-neutral JSON
protocol that is safe with legacy Windows console code pages. Optional parser
or embedding failures are recorded as warnings without discarding the lexical
index.

The default model is
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Configure it
with `CODE_HARNESS_EMBEDDING_MODEL`; batching and windowing use the corresponding
`CODE_HARNESS_EMBEDDING_*` variables documented in `config.example.yaml`. Models
are cached outside temporary storage; override the location with
`CODE_HARNESS_MODEL_CACHE`. The CLI and embedding worker use the operating
system trust store by default. Enterprise installations may set
`CODE_HARNESS_CA_BUNDLE` to an explicit PEM bundle.

## Python API

```python
from code_harness import CodeHarness

harness = CodeHarness.open("C:/projetos/nbs")
harness.index_project()
result = harness.search_text(
    query="montar_agenda_consultor",
    include_globs=("*.pck", "*.sql"),
    max_results=50,
)

for hit in result.data:
    print(hit.snippet.location.path, hit.snippet.location.start_line)

for item in harness.find_symbol("AgendaService").data:
    print(item.symbol.qualified_name, item.symbol.location.path)

for hit in harness.semantic_search("como a agenda funciona").data:
    print(hit.score, hit.snippet.location.path)

for hit in harness.search_code("como AgendaService monta a agenda").data:
    print(hit.score, [e.match_type for e in hit.evidence])

context = harness.build_context("como a agenda funciona?", max_tokens=12_000).data
print(context.estimated_tokens, context.snippets)

repository_map = harness.get_repository_map().data
print(repository_map.root)
```

The API returns typed Python objects, never interface-specific dictionaries.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest --cov
```

See the [project status](docs/project-status.md),
[architecture](docs/architecture.md), [tools](docs/tools.md), and the
[implementation roadmap](docs/implementation-plan.md).

> Teste de fluxo de commit: alteração mínima no README.
> teste fluxo commit 2
