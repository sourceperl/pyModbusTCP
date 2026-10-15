#!/usr/bin/env bash
#
# Build the Sphinx documentation (docs/) in a temporary virtual environment, removed at the end.
#
# Usage:
#   tools/build_docs.sh [-p PYTHON] [-b BUILDER] [-o OUT_DIR] [-h]
#
# Options:
#   -p PYTHON    Python interpreter to use, 3.12 or later (default: python3)
#   -b BUILDER   Sphinx builder: html, epub, latex... (default: html)
#   -o OUT_DIR   output directory (default: docs/_build/BUILDER)
#   -h           show this help
#
# The build is strict, like on Read the Docs and in the CI: any warning is an error.

set -euo pipefail

PYTHON="python3"
BUILDER="html"
OUT_DIR=""

usage() {
    sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "p:b:o:h" opt; do
    case "$opt" in
        p) PYTHON="$OPTARG" ;;
        b) BUILDER="$OPTARG" ;;
        o) OUT_DIR="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# a relative OUT_DIR is relative to the current directory: make it absolute before moving to the project root
if [[ -n "$OUT_DIR" && "$OUT_DIR" != /* ]]; then
    OUT_DIR="$PWD/$OUT_DIR"
fi

# move to the project root (parent of the script directory)
cd "$(dirname "$0")/.."

if [[ ! -f docs/conf.py || ! -f docs/requirements.txt ]]; then
    echo "Error: docs/conf.py or docs/requirements.txt not found in $(pwd)" >&2
    exit 1
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: interpreter '$PYTHON' not found" >&2
    exit 1
fi

# Sphinx (docs/requirements.txt) needs Python 3.12 or later
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Error: the documentation needs Python 3.12 or later ($("$PYTHON" --version) found), use -p" >&2
    exit 1
fi

OUT_DIR="${OUT_DIR:-docs/_build/$BUILDER}"

# temporary venv (and Sphinx cache), always removed, even on error or ctrl+c
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pymodbustcp-docs.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> Using Python: $("$PYTHON" --version) ($(command -v "$PYTHON"))"
echo "==> Creating a temporary venv: $TMP_DIR/venv"
"$PYTHON" -m venv "$TMP_DIR/venv"
VENV_PY="$TMP_DIR/venv/bin/python"

echo "==> Installing the documentation requirements"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check --requirement docs/requirements.txt

# the library is imported from this checkout by docs/conf.py: no install needed
# -d: Sphinx cache out of OUT_DIR (always a full build, and no cache files in an EPUB or an HTML zip)
echo "==> Building the documentation ($BUILDER)"
rm -rf "$OUT_DIR"
"$VENV_PY" -m sphinx -W --keep-going -b "$BUILDER" -d "$TMP_DIR/doctrees" docs "$OUT_DIR"

echo ""
echo "Documentation ready in $OUT_DIR/"
if [[ "$BUILDER" == "html" ]]; then
    echo "Open it: $OUT_DIR/index.html"
fi
