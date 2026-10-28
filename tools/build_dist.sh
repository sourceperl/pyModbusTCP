#!/usr/bin/env bash
#
# Build the two packages of the repository (pyModbusTCP and pyModbusTCP-cli) for PyPI with the current Python (the
# activated dev venv of ./init_dev_venv.sh: build, twine and hatchling), then check them before an upload with twine.
#
# Usage:
#   tools/build_dist.sh [-p PYTHON] [-o OUT_DIR] [-t] [-c] [-h]
#
# Options:
#   -p PYTHON    Python interpreter with build, twine and hatchling (default: python, the one of the active venv)
#   -o OUT_DIR   output directory: new, empty, or with packages only, removed first (default: dist)
#   -t           also test the install of the built wheels, in throwaway venvs (needs pyserial from PyPI)
#   -c           check only, not a release (the CI): no check of the date of CHANGES, no lookup on PyPI/TestPyPI
#   -h           show this help
#
# No isolated build environment: hatchling is taken from PYTHON (python -m build --no-isolation).
# Checks: "twine check" on the 4 files (sdist and wheel of each package), the version of both packages is VERSION in
# pyModbusTCP/constants.py, pyModbusTCP-cli requires exactly this release and the cli extra exactly this release of
# pyModbusTCP-cli (tools/check_dist_requires.py), the pre-release pins of the docs name VERSION without its .devN
# suffix (an error for a release), and a warning if VERSION is already on PyPI or TestPyPI (an uploaded version can
# never be uploaded again). With -t: the library alone (no mbtcp-* command, no pyserial), then with pyModbusTCP-cli
# (the 5 commands), each in a fresh venv.

set -euo pipefail

PYTHON="python"
OUT_DIR=""
INSTALL_TEST=0
RELEASE=1

usage() {
    sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "p:o:tch" opt; do
    case "$opt" in
        p) PYTHON="$OPTARG" ;;
        o) OUT_DIR="$OPTARG" ;;
        t) INSTALL_TEST=1 ;;
        c) RELEASE=0 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# a relative OUT_DIR is relative to the current directory: make it absolute before moving to the project root
if [[ -n "$OUT_DIR" && "$OUT_DIR" != /* ]]; then
    OUT_DIR="$PWD/$OUT_DIR"
fi
# a relative path to the interpreter (like venv/bin/python): resolve it from the current directory too
if [[ "$PYTHON" == */* ]]; then
    PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"
fi

# move to the project root (parent of the script directory)
cd "$(dirname "$0")/.."

CLI_DIR="pyModbusTCP-cli"
if [[ ! -f pyproject.toml || ! -f "$CLI_DIR/pyproject.toml" || ! -f pyModbusTCP/constants.py ]]; then
    echo "Error: pyproject.toml, $CLI_DIR/pyproject.toml or pyModbusTCP/constants.py not found in $(pwd)" >&2
    exit 1
fi

# venv_bin
. tools/_lib.sh

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: interpreter '$PYTHON' not found" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import build, twine, hatchling' 2>/dev/null; then
    echo "Error: build, twine or hatchling missing for $(command -v "$PYTHON"): activate the dev venv" \
         "(./init_dev_venv.sh, then source $(venv_bin venv)/activate) or use -p" >&2
    exit 1
fi

OUT_DIR="${OUT_DIR:-$PWD/dist}"
# the old packages of OUT_DIR are removed, nothing else: OUT_DIR must be a new or empty directory, or one with packages
# only (*.whl, *.tar.gz: an older dist/), never a directory of something else given by mistake (like -o tools)
if [[ -e "$OUT_DIR" && ! -d "$OUT_DIR" ]]; then
    echo "Error: OUT_DIR '$OUT_DIR' is not a directory" >&2
    exit 1
fi
if [[ -d "$OUT_DIR" ]]; then
    # (find -mindepth/-maxdepth: GNU and BSD find; the dot files count too)
    other="$(find "$OUT_DIR" -mindepth 1 -maxdepth 1 ! \( -type f \( -name '*.whl' -o -name '*.tar.gz' \) \) \
             | head -n 1)"
    if [[ -n "$other" ]]; then
        echo "Error: OUT_DIR '$OUT_DIR' has other files than packages (like '$(basename "$other")'): use a new," \
             "empty or dist directory" >&2
        exit 1
    fi
fi

# the version of both packages, read without importing the library
VERSION="$(sed -n "s/^VERSION = ['\"]\([^'\"]*\)['\"].*/\1/p" pyModbusTCP/constants.py)"
if [[ -z "$VERSION" ]]; then
    echo "Error: VERSION not found in pyModbusTCP/constants.py" >&2
    exit 1
fi

echo "==> Using Python: $("$PYTHON" --version) ($(command -v "$PYTHON"))"
echo "==> Version: $VERSION (pyModbusTCP/constants.py)"

# the changelog of a release (not a .devN version) starts with this version and its release date (YYYY-MM-DD): the
# changelog is in the sdist and on the docs, a placeholder like "xxxx-xx-xx" can't be fixed after the upload
if [[ "$RELEASE" -eq 1 && "$VERSION" != *.dev* ]]; then
    changes_head="$(grep -m 1 -E '^[0-9]' CHANGES || true)"
    if [[ ! "$changes_head" =~ ^${VERSION//./\\.}\ [0-9]{4}-[0-9]{2}-[0-9]{2}( |$) ]]; then
        echo "Error: the first entry of CHANGES must be \"$VERSION YYYY-MM-DD\", found: \"$changes_head\"" >&2
        exit 1
    fi
fi
# the install commands of the docs pin a pre-release: the one they document, VERSION without its .devN suffix (a
# development version 0.4.0a4.dev0 documents 0.4.0a4). An error for a release (the docs are in the sdist and on
# readthedocs), a warning otherwise
PIN_VERSION="${VERSION%%.dev*}"
stale="$(grep -rnoE '==[0-9]+\.[0-9]+\.[0-9]+(a|b|rc)[0-9]+' README.md "$CLI_DIR/README.md" docs --include='*.md' \
         --include='*.rst' | grep -vE "==${PIN_VERSION//./\\.}\$" || true)"
if [[ -n "$stale" ]]; then
    if [[ "$RELEASE" -eq 1 && "$VERSION" != *.dev* ]]; then
        echo "Error: pre-release pins other than $PIN_VERSION in the docs:" >&2
        echo "$stale" >&2
        exit 1
    fi
    echo "Warning: pre-release pins other than $PIN_VERSION in the docs:" >&2
    echo "$stale" >&2
fi

# no stale file of an older version next to the new ones (twine upload dist/* would take them)
if [[ -d "$OUT_DIR" ]]; then
    find "$OUT_DIR" -mindepth 1 -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' \) -exec rm -f {} +
fi
rm -rf build "$CLI_DIR/build"
mkdir -p "$OUT_DIR"

echo "==> Building pyModbusTCP"
"$PYTHON" -m build --no-isolation --outdir "$OUT_DIR" .
echo "==> Building pyModbusTCP-cli"
"$PYTHON" -m build --no-isolation --outdir "$OUT_DIR" "$CLI_DIR"

echo "==> Checking the files"
# file names normalized by the build backend (PEP 625 and PEP 427): lower case, "-" to "_"
LIB_WHL="$OUT_DIR/pymodbustcp-$VERSION-py3-none-any.whl"
CLI_WHL="$OUT_DIR/pymodbustcp_cli-$VERSION-py3-none-any.whl"
expected=("$OUT_DIR/pymodbustcp-$VERSION.tar.gz" "$LIB_WHL" "$OUT_DIR/pymodbustcp_cli-$VERSION.tar.gz" "$CLI_WHL")
for file in "${expected[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "Error: $(basename "$file") not built, found: $(ls "$OUT_DIR")" >&2
        exit 1
    fi
done
if [[ "$(ls "$OUT_DIR" | wc -l)" -ne ${#expected[@]} ]]; then
    echo "Error: unexpected files in $OUT_DIR: $(ls "$OUT_DIR")" >&2
    exit 1
fi
"$PYTHON" -m twine check --strict "${expected[@]}"

# pyModbusTCP-cli has no code: it requires exactly this release of the library, and the cli extra of the library
# exactly this release of pyModbusTCP-cli (set by hatch_metadata.py and pyModbusTCP-cli/hatch_metadata_cli.py)
"$PYTHON" tools/check_dist_requires.py "$OUT_DIR"

if [[ "$INSTALL_TEST" -eq 1 ]]; then
    # throwaway venvs: the dev venv has the editable installs of this checkout, it can't test the wheels
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pymodbustcp-dist.XXXXXX")"
    trap 'rm -rf "$TMP_DIR"' EXIT

    echo "==> Install test: the library alone"
    "$PYTHON" -m venv "$TMP_DIR/lib-only"
    BIN_LIB="$(venv_bin "$TMP_DIR/lib-only")"
    "$BIN_LIB/python" -m pip install --quiet --disable-pip-version-check "$LIB_WHL"
    if "$BIN_LIB/python" -m pip show --quiet pyModbusTCP-cli >/dev/null 2>&1 \
            || "$BIN_LIB/python" -m pip show --quiet pyserial >/dev/null 2>&1; then
        echo "Error: pyModbusTCP-cli or pyserial installed with the library alone" >&2
        exit 1
    fi
    if compgen -G "$BIN_LIB/mbtcp-*" >/dev/null; then
        echo "Error: mbtcp-* command installed with the library alone" >&2
        exit 1
    fi
    "$BIN_LIB/python" -m pyModbusTCP.tools server --version
    # a gateway without pyserial: exit status 2, with the way to install it
    status=0
    "$BIN_LIB/python" -m pyModbusTCP.tools tcp2rtu --help 2>"$TMP_DIR/err.txt" >/dev/null || status=$?
    if [[ "$status" -ne 2 ]] || ! grep -qF 'pyModbusTCP[serial]' "$TMP_DIR/err.txt"; then
        echo "Error: a gateway without pyserial must exit with status 2 and the install command, got $status:" >&2
        cat "$TMP_DIR/err.txt" >&2
        exit 1
    fi

    echo "==> Install test: the library with pyModbusTCP-cli (pyserial from the package index)"
    "$PYTHON" -m venv "$TMP_DIR/with-cli"
    BIN_CLI="$(venv_bin "$TMP_DIR/with-cli")"
    # both wheels in one call: the library comes from OUT_DIR, never from the index (where VERSION may already exist);
    # pyserial at the version of the CI. The wheels by their name, from OUT_DIR: Git Bash (Windows) converts a path
    # like /d/a/dist/x.whl for the Windows python only when it is the whole argument, never "/d/a/dist/x.whl[serial]"
    ci_requirements="$PWD/requirements/ci.txt"
    (cd "$OUT_DIR" && "$BIN_CLI/python" -m pip install --quiet --disable-pip-version-check -c "$ci_requirements" \
        "./$(basename "$LIB_WHL")[serial]" "./$(basename "$CLI_WHL")")
    for cmd in mbtcp-server mbtcp-client mbtcp-tcp2rtu mbtcp-rtu2tcp mbtcp-rtu-scan; do
        if ! "$BIN_CLI/$cmd" --version | grep -qF "$VERSION"; then
            echo "Error: '$cmd --version' doesn't show $VERSION" >&2
            exit 1
        fi
    done
    "$BIN_CLI/mbtcp-client" --version
fi

if [[ "$RELEASE" -eq 0 ]]; then
    echo ""
    echo "Packages built and checked in $OUT_DIR/ (-c: not a release)"
    exit 0
fi

# a version can never be uploaded twice: warn if it is already on PyPI or TestPyPI (skipped when offline)
already_released() {
    "$PYTHON" -c '
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=10)
except urllib.error.HTTPError:
    sys.exit(1)  # 404: this version is not released
except OSError:
    sys.exit(2)  # offline
' "$1" 2>/dev/null
}
for index in pypi.org test.pypi.org; do
    for project in pyModbusTCP pyModbusTCP-cli; do
        if already_released "https://$index/pypi/$project/$VERSION/json"; then
            echo "Warning: $project $VERSION is already on $index, the upload will be refused: set a new VERSION" >&2
        fi
    done
done

echo ""
echo "Packages ready in $OUT_DIR/:"
ls -1 "$OUT_DIR"
echo ""
echo "Upload them (see HOWTO-PyPi.md), the library first, twine asks for the API token of each project:"
for repo in testpypi pypi; do
    echo "  twine upload -r $repo $OUT_DIR/pymodbustcp-$VERSION*"
    echo "  twine upload -r $repo $OUT_DIR/pymodbustcp_cli-$VERSION*"
done
