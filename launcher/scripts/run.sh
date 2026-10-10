#!/bin/sh
set -eu

# Preserve the caller's working directory so relative plan/input paths retain
# their meaning. The installed skill contains the package alongside SKILL.md;
# the source distribution keeps it one directory above launcher/.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
skill_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
package_dir=$skill_dir
if [ ! -d "$package_dir/research_workbench" ]; then
    package_dir=$(CDPATH= cd -- "$skill_dir/.." && pwd)
fi
if [ ! -d "$package_dir/research_workbench" ]; then
    echo 'Research Workbench package is missing; install the complete distribution.' >&2
    exit 2
fi
PYTHONPATH="$package_dir${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

supports_workbench_python() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

workbench_python=
if [ -n "${RESEARCH_WORKBENCH_PYTHON:-}" ] && supports_workbench_python "$RESEARCH_WORKBENCH_PYTHON"; then
    workbench_python=$RESEARCH_WORKBENCH_PYTHON
fi
if [ -z "$workbench_python" ]; then
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_workbench_python "$candidate"; then
            workbench_python=$candidate
            break
        fi
    done
fi
if [ -z "$workbench_python" ]; then
    workbench_codex_home=${CODEX_HOME:-"$HOME/.codex"}
    candidate="$workbench_codex_home/skills/forcefield-generator/.venv/bin/python"
    if [ -x "$candidate" ] && supports_workbench_python "$candidate"; then
        workbench_python=$candidate
    fi
fi
if [ -z "$workbench_python" ]; then
    echo 'Research Workbench requires Python 3.11 or newer. Set RESEARCH_WORKBENCH_PYTHON to an existing supported interpreter.' >&2
    exit 2
fi
exec "$workbench_python" -m research_workbench "$@"
