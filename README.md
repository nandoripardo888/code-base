# code-harness

`code-harness` is a local-first Python library and CLI for retrieving verifiable
context from source repositories. The first release focuses on safe filesystem
access and direct lexical search. Indexing, structural parsing, semantic search,
and MCP are deliberately staged behind the same application contracts.

## Current capabilities

- discover text files while honoring safe defaults and `.gitignore`;
- find files by name or path;
- search literal text and regular expressions with Ripgrep;
- read complete files or line ranges;
- reject paths and symlinks that escape the project root;
- return immutable, structured Python results with paths, lines, hashes, scores,
  timings, and warnings;
- expose the same application tools through Python and the CLI.

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

code-harness init "C:\projetos\sample_project"
code-harness files list
code-harness files search "AgendaService"
code-harness search text "AgendaService"
code-harness search regex "public\s+void\s+setFilter"
code-harness read "src\AgendaService.java" --lines 100:180
```

Linux activation uses `source .venv/bin/activate`. A project can also be selected
without changing the active registration:

```bash
code-harness --project /work/repository search text "needle"
CODE_HARNESS_PROJECT=/work/repository code-harness files list
```

Use `--output json`, `jsonl`, `text`, `table`, or `llm` before the subcommand.
Use `python -m code_harness` interchangeably with `code-harness`.

## Python API

```python
from code_harness import CodeHarness

harness = CodeHarness.open("C:/projetos/nbs")
result = harness.search_text(
    query="montar_agenda_consultor",
    include_globs=("*.pck", "*.sql"),
    max_results=50,
)

for hit in result.data:
    print(hit.snippet.location.path, hit.snippet.location.start_line)
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

See [architecture](docs/architecture.md), [tools](docs/tools.md), and the
[implementation roadmap](docs/implementation-plan.md).

> Teste de fluxo de commit: alteração mínima no README.
> teste fluxo commit 2