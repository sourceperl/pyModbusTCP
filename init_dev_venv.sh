#!/usr/bin/env bash
#
# Set up a virtual environment for testing and building pyModbusTCP and its command line tools.
#
# Usage:
#   ./init_dev_venv.sh [-p PYTHON] [-d VENV_DIR] [-l] [-r] [-h]
#
# Options:
#   -p PYTHON    Python interpreter to use (default: python3)
#   -d VENV_DIR  virtual environment directory (default: venv)
#   -l           library only: no pyserial and no mbtcp-* commands (pyModbusTCP-cli/)
#   -r           recreate the venv if it already exists
#   -h           show this help

set -euo pipefail

PYTHON="python3"
VENV_DIR="venv"
RECREATE=0
WITH_TOOLS=1

# package pyModbusTCP-cli: the mbtcp-* commands of the tools of the library (pyModbusTCP.tools)
TOOLS_DIR="pyModbusTCP-cli"

# test and build tools (types-pyserial: type stubs of pyserial, for mypy on the gateways of pyModbusTCP.tools;
# hatchling: the build backend, installed here only for mypy on pyModbusTCP-cli/hatch_build.py)
DEV_PACKAGES=(build twine pytest flake8 mypy ruff types-pyserial hatchling)

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

# move to the project root (script directory)
cd "$(dirname "$(readlink -f "$0")")"

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

echo "==> Using Python: $($PYTHON --version) ($(command -v "$PYTHON"))"

if [[ -d "$VENV_DIR" ]]; then
    if [[ "$RECREATE" -eq 1 ]]; then
        echo "==> Removing existing venv: $VENV_DIR"
        rm -rf "$VENV_DIR"
    else
        echo "==> Reusing existing venv: $VENV_DIR (use -r to recreate it)"
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating venv: $VENV_DIR"
    # --copies: real interpreter files (not links to the system one), needed to give the CAP_NET_BIND_SERVICE
    # capability to venv/bin/python3 (listen on port 502 without root, see examples/README.md)
    "$PYTHON" -m venv --copies "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

echo "==> Installing dev tools: ${DEV_PACKAGES[*]}"
"$VENV_PY" -m pip install --upgrade "${DEV_PACKAGES[@]}"

if [[ "$WITH_TOOLS" -eq 1 ]]; then
    # both in one call: the requirement of the tools package on pyModbusTCP is satisfied by this checkout, not by PyPI
    echo "==> Installing the library (with pyserial) and the mbtcp-* commands in editable mode"
    "$VENV_PY" -m pip install --editable ".[serial]" --editable "$TOOLS_DIR"
else
    echo "==> Installing the library in editable mode"
    "$VENV_PY" -m pip install --editable .
fi

cat <<EOF

Environment ready.

  Activate the venv:  source $VENV_DIR/bin/activate

  Library (with its tools, pyModbusTCP.tools):
    Run the tests:    python -m pytest
    Code audit:       tools/code_audit.sh
    Type check:       mypy --strict pyModbusTCP
    Run a tool:       python -m pyModbusTCP.tools {server,client,tcp2rtu,rtu2tcp} --help
    Build:            python -m build --outdir dist .
    Check the build:  twine check dist/*
EOF

if [[ "$WITH_TOOLS" -eq 1 ]]; then
    cat <<EOF

  Commands (package pyModbusTCP-cli, $TOOLS_DIR/):
    Commands:         mbtcp-server, mbtcp-client, mbtcp-tcp2rtu, mbtcp-rtu2tcp (--help for each)
    Build:            python -m build --outdir dist $TOOLS_DIR
    Check the build:  twine check dist/*
EOF
fi
