# Patch MCP black-box tests

This suite exercises the installed server only through its public MCP stdio
interface. It deliberately does not import `code_harness` implementation
modules.

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q blackbox_tests
```

On Linux or macOS, run:

```bash
./.venv/bin/python -m pytest -q blackbox_tests
```

To run only the patch MCP scenarios during development, use:

```powershell
.\.venv\Scripts\python.exe -m pytest -q blackbox_tests/test_patch_mcp.py
```

Each test starts a fresh MCP server against a temporary project and stores patch
history in another temporary directory. The tests require Git and the `mcp`
Python client dependency.

> Teste de edição realizado via ferramenta `@Code`.
