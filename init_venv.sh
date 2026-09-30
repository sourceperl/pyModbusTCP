#!/usr/bin/env bash
#
# Set up a virtual environment for testing and building pyModbusTCP.
#
# Usage:
#   ./init_venv.sh [-p PYTHON] [-d VENV_DIR] [-r] [-h]
#
# Options:
#   -p PYTHON    Python interpreter to use (default: python3)
#   -d VENV_DIR  virtual environment directory (default: venv)
#   -r           recreate the venv if it already exists
#   -h           show this help

set -euo pipefail

PYTHON="python3"
VENV_DIR="venv"
RECREATE=0

# test and build tools
DEV_PACKAGES=(build twine pytest)

usage() {
    sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "p:d:rh" opt; do
    case "$opt" in
        p) PYTHON="$OPTARG" ;;
        d) VENV_DIR="$OPTARG" ;;
        r) RECREATE=1 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# move to the project root (script directory)
cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -f pyproject.toml && ! -f setup.py ]]; then
    echo "Error: neither pyproject.toml nor setup.py found in $(pwd)" >&2
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
    "$PYTHON" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

echo "==> Upgrading pip, setuptools and wheel"
"$VENV_PY" -m pip install --upgrade pip setuptools wheel

echo "==> Installing tools: ${DEV_PACKAGES[*]}"
"$VENV_PY" -m pip install --upgrade "${DEV_PACKAGES[@]}"

echo "==> Installing the project in editable mode"
"$VENV_PY" -m pip install --editable .

cat <<EOF

Environment ready.

  Activate the venv:  source $VENV_DIR/bin/activate
  Run the tests:      python -m pytest
  Build:              python -m build
  Check the build:    twine check dist/*
EOF