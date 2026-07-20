#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv_path="${CODE_HARNESS_VENV:-$repo_root/.venv}"
extras="dev"

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3.12+ is required." >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "Python 3.12+ is required; the current python3 is too old." >&2
  exit 1
}
command -v rg >/dev/null 2>&1 || {
  echo "Ripgrep is required; install it and retry." >&2
  exit 1
}

[ -d "$venv_path" ] || python3 -m venv "$venv_path"
venv_python="$venv_path/bin/python"
venv_harness="$venv_path/bin/code-harness"
"$venv_python" -m pip install --upgrade pip

if [ "${CODE_HARNESS_SETUP_SEMANTIC:-0}" = "1" ]; then
  extras="$extras,semantic"
fi
if [ "${CODE_HARNESS_SETUP_PARSERS:-0}" = "1" ]; then
  extras="$extras,parsers"
fi

if [ "${CODE_HARNESS_SETUP_SEMANTIC:-0}" = "1" ]; then
  "$venv_python" -m pip install \
    -c "$repo_root/constraints/semantic.txt" -e "$repo_root[$extras]"
  CODE_HARNESS_SEMANTIC=1 "$venv_harness" --project "$repo_root" models prepare
  CODE_HARNESS_SEMANTIC=1 "$venv_harness" --project "$repo_root" doctor --deep
else
  "$venv_python" -m pip install -e "$repo_root[$extras]"
  "$venv_harness" --project "$repo_root" doctor
fi

echo "Environment ready: $venv_path"
