#!/usr/bin/env bash
#
# Build the Sphinx documentation (docs/) in a temporary virtual environment, removed at the end.
#
# Usage:
#   tools/build_docs.sh [-p PYTHON] [-b BUILDER] [-o OUT_DIR] [-h]
#
# Options:
#   -p PYTHON    Python interpreter to use, 3.12 or later (default: python3)
#   -b BUILDER   html, pdf, epub or any other Sphinx builder (default: html)
#   -o OUT_DIR   output directory (default: docs/_build/BUILDER)
#   -h           show this help
#
# The build is strict, like on Read the Docs and in the CI: any warning is an error.
# The PDF is made with LaTeX: it needs a TeX distribution (see the error message if it's missing).

set -euo pipefail

PYTHON="python3"
BUILDER="html"
OUT_DIR=""

usage() {
    sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
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
# venv_bin
. tools/_lib.sh

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

# the PDF is made by LaTeX from the output of the Sphinx latex builder: check the TeX tools before any long step
if [[ "$BUILDER" == "pdf" ]]; then
    missing=()
    for cmd in make latexmk pdflatex; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Error: the PDF needs a TeX distribution, not found: ${missing[*]}" >&2
        echo "  Debian/Ubuntu: sudo apt install make latexmk texlive-latex-recommended texlive-latex-extra \\" >&2
        echo "                 texlive-fonts-recommended tex-gyre" >&2
        echo "  macOS:         MacTeX (https://tug.org/mactex/)" >&2
        exit 1
    fi
fi

OUT_DIR="${OUT_DIR:-docs/_build/$BUILDER}"

# temporary venv (and Sphinx cache), always removed, even on error or ctrl+c
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pymodbustcp-docs.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> Using Python: $("$PYTHON" --version) ($(command -v "$PYTHON"))"
echo "==> Creating a temporary venv: $TMP_DIR/venv"
"$PYTHON" -m venv "$TMP_DIR/venv"
VENV_PY="$(venv_bin "$TMP_DIR/venv")/python"

echo "==> Installing the documentation requirements"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check --requirement docs/requirements.txt

# the library is imported from this checkout by docs/conf.py: no install needed
# -d: Sphinx cache out of OUT_DIR (always a full build, and no cache files in an EPUB or an HTML zip)
echo "==> Building the documentation ($BUILDER)"
rm -rf "$OUT_DIR"
if [[ "$BUILDER" == "pdf" ]]; then
    # LaTeX sources in the temporary directory, only the PDF goes to OUT_DIR
    LATEX_DIR="$TMP_DIR/latex"
    "$VENV_PY" -m sphinx -W --keep-going -b latex -d "$TMP_DIR/doctrees" docs "$LATEX_DIR"
    echo "==> Compiling the PDF with LaTeX (log: $OUT_DIR/latex.log on error)"
    if ! make -C "$LATEX_DIR" all-pdf LATEXMKOPTS="-interaction=nonstopmode -halt-on-error" \
            > "$TMP_DIR/latex.log" 2>&1; then
        mkdir -p "$OUT_DIR"
        cp "$TMP_DIR/latex.log" "$OUT_DIR/latex.log"
        grep -E '^!' -A 2 "$TMP_DIR/latex.log" >&2 || tail -n 20 "$TMP_DIR/latex.log" >&2
        echo "Error: the LaTeX compilation failed, see $OUT_DIR/latex.log" >&2
        exit 1
    fi
    mkdir -p "$OUT_DIR"
    cp "$LATEX_DIR"/*.pdf "$OUT_DIR/"
else
    "$VENV_PY" -m sphinx -W --keep-going -b "$BUILDER" -d "$TMP_DIR/doctrees" docs "$OUT_DIR"
fi

echo ""
echo "Documentation ready in $OUT_DIR/"
case "$BUILDER" in
    html) echo "Open it: $OUT_DIR/index.html" ;;
    pdf) echo "Open it: $(ls "$OUT_DIR"/*.pdf)" ;;
esac
