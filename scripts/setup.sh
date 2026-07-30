#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${1:-.venv}"
VENV_PATH="$REPO_ROOT/$VENV"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.12+ is required." >&2
  exit 1
fi
if ! command -v rg >/dev/null 2>&1; then
  echo "Ripgrep (rg) is required." >&2
  exit 1
fi

if [[ ! -d "$VENV_PATH" ]]; then
  python3 -m venv "$VENV_PATH"
fi

"$VENV_PATH/bin/python" -m pip install --upgrade pip
"$VENV_PATH/bin/python" -m pip install -e "$REPO_ROOT[dev]"

echo "Environment ready: $VENV_PATH"
echo "Try: code-harness grep hello --project $REPO_ROOT"
