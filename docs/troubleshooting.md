# Troubleshooting

## `ripgrep_unavailable`

Install Ripgrep and ensure `rg --version` succeeds, or configure the executable
through `CODE_HARNESS_RG`.

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
