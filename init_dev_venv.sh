#!/usr/bin/env bash
#
# Set up a virtual environment for testing and building pyModbusTCP and its command line tools.
#
# Usage:
#   ./init_dev_venv.sh [-p PYTHON] [-d VENV_DIR] [-l] [-r] [-h]
#
# Options:
#   -p PYTHON    Python interpreter to use (default: python3), a relative path is relative to the current directory
#   -d VENV_DIR  virtual environment directory, relative to the project root (default: venv)
#   -l           library only: no pyserial and no mbtcp-* commands (pyModbusTCP-cli/)
#   -r           recreate the venv if it already exists (only a venv is removed, never another directory)
#   -h           show this help

set -euo pipefail

PYTHON="python3"
VENV_DIR="venv"
RECREATE=0
WITH_TOOLS=1

# package pyModbusTCP-cli: the mbtcp-* commands of the tools of the library (pyModbusTCP.tools)
TOOLS_DIR="pyModbusTCP-cli"

# test and build tools, each at an exact version (the ones of the CI, and twine, ruff...): see this file
DEV_REQUIREMENTS="requirements/dev.txt"

usage() {
    sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "p:d:lrh" opt; do
    case "$opt" in
        p) PYTHON="$OPTARG" ;;
        d) VENV_DIR="$OPTARG" ;;
        l) WITH_TOOLS=0 ;;
        r) RECREATE=1 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# a relative path to the interpreter (like ../other/venv/bin/python): resolved from the current directory, before the
# move to the project root (like tools/build_dist.sh); a command name (python3.13) is looked up in the PATH
if [[ "$PYTHON" == */* ]]; then
    if ! python_dir="$(cd "$(dirname "$PYTHON")" 2>/dev/null && pwd)"; then
        echo "Error: interpreter '$PYTHON' not found" >&2
        exit 1
    fi
    PYTHON="$python_dir/$(basename "$PYTHON")"
fi

# move to the project root (script directory)
cd "$(dirname "$0")"
# venv_bin
. tools/_lib.sh

if [[ ! -f pyproject.toml ]]; then
    echo "Error: pyproject.toml not found in $(pwd)" >&2
    exit 1
fi

if [[ "$WITH_TOOLS" -eq 1 && ! -f "$TOOLS_DIR/pyproject.toml" ]]; then
    echo "Error: $TOOLS_DIR/pyproject.toml not found (use -l to set up the library only)" >&2
    exit 1
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: interpreter '$PYTHON' not found" >&2
    exit 1
fi

echo "==> Using Python: $("$PYTHON" --version) ($(command -v "$PYTHON"))"

if [[ -z "$VENV_DIR" ]]; then
    echo "Error: VENV_DIR must not be empty" >&2
    exit 1
fi
if [[ -e "$VENV_DIR" && ! -d "$VENV_DIR" ]]; then
    echo "Error: VENV_DIR '$VENV_DIR' is not a directory" >&2
    exit 1
fi
if [[ -d "$VENV_DIR" ]]; then
    # VENV_DIR is never the project or one of its parents (like -d . or -d ..): with -r, rm -rf would delete it
    # (physical paths: a symbolic link to the project is the project)
    venv_abs="$(cd "$VENV_DIR" && pwd -P)"
    if [[ "$(pwd -P)/" == "${venv_abs%/}/"* ]]; then
        echo "Error: VENV_DIR '$VENV_DIR' is the project directory or one of its parents: use a new directory," \
             "like venv" >&2
        exit 1
    fi
    # an existing directory is used (or removed by -r) only if it's a venv (pyvenv.cfg), or empty: never a directory
    # of something else given by mistake (like -d tools)
    if [[ ! -f "$VENV_DIR/pyvenv.cfg" && -n "$(ls -A "$VENV_DIR")" ]]; then
        echo "Error: VENV_DIR '$VENV_DIR' exists and is not a virtual environment (no pyvenv.cfg): use a new," \
             "empty or venv directory" >&2
        exit 1
    fi
    if [[ ! -f "$VENV_DIR/pyvenv.cfg" ]]; then
        # an empty directory: the venv is created in it (python -m venv accepts it)
        :
    elif [[ "$RECREATE" -eq 1 ]]; then
        echo "==> Removing existing venv: $VENV_DIR"
        rm -rf "$VENV_DIR"
    else
        echo "==> Reusing existing venv: $VENV_DIR (use -r to recreate it)"
    fi
fi

if [[ ! -f "$VENV_DIR/pyvenv.cfg" ]]; then
    echo "==> Creating venv: $VENV_DIR"
    "$PYTHON" -m venv --copies "$VENV_DIR"
fi

VENV_BIN="$(venv_bin "$VENV_DIR")"
VENV_PY="$VENV_BIN/python"

# pip itself: always its latest release (not pinned); the tools below: their exact version
echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

echo "==> Installing dev tools: $DEV_REQUIREMENTS"
"$VENV_PY" -m pip install --requirement "$DEV_REQUIREMENTS"

if [[ "$WITH_TOOLS" -eq 1 ]]; then
    # both in one call: the requirement of the tools package on pyModbusTCP is satisfied by this checkout, not by PyPI
    echo "==> Installing the library (with pyserial) and the mbtcp-* commands in editable mode"
    # no build isolation: built by the hatchling of the dev tools, not by the latest release
    "$VENV_PY" -m pip install --no-build-isolation --editable ".[serial]" --editable "$TOOLS_DIR"
else
    echo "==> Installing the library in editable mode"
    "$VENV_PY" -m pip install --no-build-isolation --editable .
    # library only: without pyserial (pinned with the tools of the CI, where the gateways are always tested)
    "$VENV_PY" -m pip uninstall --yes --quiet pyserial
fi

cat <<EOF

Environment ready.

  Activate the venv:  source $VENV_BIN/activate

  Library (with its tools, pyModbusTCP.tools):
    Run the tests:    python -m pytest
    Code audit:       tools/code_audit.sh
    Type check:       mypy --strict pyModbusTCP
    Run a tool:       python -m pyModbusTCP.tools {server,client,tcp2rtu,rtu2tcp,rtu-scan} --help
    Build:            python -m build --no-isolation --outdir dist .
    Check the build:  twine check dist/*
EOF

if [[ "$WITH_TOOLS" -eq 1 ]]; then
    cat <<EOF

  Commands (package pyModbusTCP-cli, $TOOLS_DIR/):
    Commands:         mbtcp-server, mbtcp-client, mbtcp-tcp2rtu, mbtcp-rtu2tcp, mbtcp-rtu-scan (--help for each)
    Build:            python -m build --no-isolation --outdir dist $TOOLS_DIR
    Check the build:  twine check dist/*
EOF
fi
