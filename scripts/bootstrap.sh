#!/bin/sh
# Choose an existing Python >=3.11; do not install or change a global interpreter.
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
valid_python() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1
}
task_python=""
if [ -n "${RESEARCH_WORKBENCH_PYTHON:-}" ]; then
  if ! valid_python "$RESEARCH_WORKBENCH_PYTHON"; then
    echo 'RESEARCH_WORKBENCH_PYTHON must be an executable Python >=3.11.' >&2
    exit 2
  fi
  task_python=$RESEARCH_WORKBENCH_PYTHON
else
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && valid_python "$candidate"; then
      task_python=$(command -v "$candidate")
      break
    fi
  done
  if [ -z "$task_python" ]; then
    candidate="${CODEX_HOME:-$HOME/.codex}/skills/forcefield-generator/.venv/bin/python"
    if [ -x "$candidate" ] && valid_python "$candidate"; then
      task_python=$candidate
    fi
  fi
fi
if [ -z "$task_python" ]; then
  echo 'Python >=3.11 is required. Set RESEARCH_WORKBENCH_PYTHON to an existing interpreter.' >&2
  exit 2
fi
if [ "${1:-}" = install ]; then
  shift
  exec "$task_python" "$project_root/scripts/install.py" "$@"
fi
export PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$task_python" -m research_workbench "$@"
