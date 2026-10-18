#!/usr/bin/env bash
#
# Code audit of the repository: flake8, mypy and ruff (and optionally the tests), with the tools of a Python
# interpreter (the dev venv of ./init_dev_venv.sh), then a summary. The exit status is 1 if any check fails.
#
# Usage:
#   tools/code_audit.sh [-a] [-s] [-p PYTHON] [-o FILE] [-h] [CHECK...]
#
# Options:
#   -a           run all the checks, the tests included (same as the check "all")
#   -s           summary only: hide the outputs of the tools (the full report is still written with -o)
#   -p PYTHON    Python interpreter with the dev tools (default: the active venv, else ./venv, else python3)
#   -o FILE      also write the full report to FILE
#   -h           show this help
#
# Checks: flake8, mypy, ruff, pytest, or all (default: flake8 mypy ruff).
# The summary at the end gives the result of each check (number of issues, tests passed/failed) and its duration.
# The tools are run as modules of PYTHON (python -m mypy...), never from the PATH: the versions and the installed
# packages (pyserial, type stubs, hatchling) are those of the venv, like in the CI. mypy uses the [tool.mypy]
# settings of pyproject.toml (strict). Not audited: build/, docs/, lab/, venv/ and .venv/.

set -euo pipefail

PYTHON=""
OUT_FILE=""
RUN_ALL=0
SUMMARY_ONLY=0
ALL_CHECKS=(flake8 mypy ruff pytest)
DEFAULT_CHECKS=(flake8 mypy ruff)

# directories out of the audit (same list for every tool)
EXCLUDED_DIRS=(build docs lab venv .venv)

# packages of the dev venv (see init_dev_venv.sh) that change the results of mypy when they are missing
TYPING_PACKAGES=(types-pyserial pyserial hatchling)

usage() {
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
}

# contains WORD ITEM...: true if WORD is one of the items
contains() {
    local word="$1" item
    shift
    for item in "$@"; do
        [[ "$item" == "$word" ]] && return 0
    done
    return 1
}

while getopts "asp:o:h" opt; do
    case "$opt" in
        a) RUN_ALL=1 ;;
        s) SUMMARY_ONLY=1 ;;
        p) PYTHON="$OPTARG" ;;
        o) OUT_FILE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

# checks to run, in the order of ALL_CHECKS
if [[ $RUN_ALL -eq 1 ]]; then
    CHECKS=("${ALL_CHECKS[@]}")
elif [[ $# -eq 0 ]]; then
    CHECKS=("${DEFAULT_CHECKS[@]}")
else
    for arg in "$@"; do
        if [[ "$arg" != "all" ]] && ! contains "$arg" "${ALL_CHECKS[@]}"; then
            echo "Error: unknown check '$arg' (choose from: ${ALL_CHECKS[*]} all)" >&2
            exit 1
        fi
    done
    CHECKS=()
    for check in "${ALL_CHECKS[@]}"; do
        if contains all "$@" || contains "$check" "$@"; then
            CHECKS+=("$check")
        fi
    done
fi

# a relative OUT_FILE is relative to the current directory: make it absolute before moving to the project root
if [[ -n "$OUT_FILE" && "$OUT_FILE" != /* ]]; then
    OUT_FILE="$PWD/$OUT_FILE"
fi

# move to the project root (parent of the script directory)
cd "$(dirname "$0")/.."

if [[ ! -f pyproject.toml ]]; then
    echo "Error: pyproject.toml not found in $(pwd)" >&2
    exit 1
fi

# default interpreter: the active venv, else the dev venv of the project, else python3
if [[ -z "$PYTHON" ]]; then
    if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
        PYTHON="$VIRTUAL_ENV/bin/python"
    elif [[ -x venv/bin/python ]]; then
        PYTHON="venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: interpreter '$PYTHON' not found" >&2
    exit 1
fi

# each tool must be a module of PYTHON: stop now with the install command, not with an error in the middle of the report
missing=()
for check in "${CHECKS[@]}"; do
    "$PYTHON" -c "import $check" 2>/dev/null || missing+=("$check")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: not installed for $PYTHON: ${missing[*]}" >&2
    echo "  Install them:  $PYTHON -m pip install ${missing[*]}" >&2
    echo "  or set up the dev venv:  ./init_dev_venv.sh" >&2
    exit 1
fi

# exclusion of EXCLUDED_DIRS in the syntax of each tool
excluded_csv="$(IFS=,; echo "${EXCLUDED_DIRS[*]}")"
excluded_re="$(IFS='|'; echo "${EXCLUDED_DIRS[*]}")"
# mypy: a regex on the path (with / on every OS), the directory at any depth (like pyModbusTCP-cli/build/)
MYPY_EXCLUDE="(^|/)(${excluded_re//./\\.})/"

# results of the checks (bash 3.2 of macOS: no associative array)
RESULT_NAMES=()
RESULT_STATUS=()
RESULT_TIMES=()
RESULT_DETAILS=()

# outputs of the tools (to extract the result of each check) and the report of -s, always removed
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pymodbustcp-audit.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

# the outputs of the tools are shown as they come (not held in a buffer by Python when piped to tee)
export PYTHONUNBUFFERED=1

# progress of the checks with -s (on the terminal, the report itself goes to a file)
exec 3>&2

# check_detail NAME LOG: one line with the result of a check, from the output of its tool
check_detail() {
    local name="$1" log="$2" count
    case "$name" in
        flake8)
            # --count: the number of issues is the last line
            count="$(tail -n 1 "$log")"
            [[ "$count" =~ ^[0-9]+$ ]] && echo "$count issue(s)"
            ;;
        mypy)
            grep -E '^(Found [0-9]+ errors? in|Success:)' "$log" | tail -n 1
            ;;
        ruff)
            grep -E '^(Found [0-9]+ errors?|All checks passed)' "$log" | tail -n 1
            ;;
        pytest)
            # last line of pytest -q, like "1 failed, 171 passed, 4 skipped in 29.06s", without the duration
            grep -E '[0-9]+ (passed|failed|errors?|skipped|deselected)|no tests ran' "$log" | tail -n 1 \
                | sed -E 's/^=+ //; s/ =+$//; s/ in [0-9.]+s( \([0-9:]+\))?$//'
            ;;
    esac
}

run_check() {
    local name="$1"
    shift
    local start status log="$TMP_DIR/$name.log"
    [[ $SUMMARY_ONLY -eq 1 ]] && printf 'Running %s...\n' "$name" >&3
    echo ""
    echo "=== Run $name ==="
    echo "\$ ${*/#$PYTHON/python}"
    start=$SECONDS
    # never stop the audit on a failed check: the status is recorded for the summary (pipefail: status of the tool)
    if "$@" 2>&1 | tee "$log"; then status=0; else status=$?; fi
    RESULT_NAMES+=("$name")
    RESULT_STATUS+=("$status")
    RESULT_TIMES+=("$((SECONDS - start))")
    RESULT_DETAILS+=("$(check_detail "$name" "$log" || true)")
}

tool_version() {
    "$PYTHON" -m "$1" --version 2>/dev/null | head -n 1
}

audit() {
    echo "=== Code audit of $(basename "$PWD") ==="
    echo "Date:    $(date '+%Y-%m-%d %H:%M:%S %z')"
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        local dirty=""
        [[ -n "$(git status --porcelain 2>/dev/null)" ]] && dirty=" (with uncommitted changes)"
        echo "Commit:  $(git rev-parse --short HEAD 2>/dev/null || echo 'none') on $(git branch --show-current 2>/dev/null)$dirty"
    fi
    echo "Python:  $("$PYTHON" --version 2>&1) ($(command -v "$PYTHON"))"
    local check
    for check in "${CHECKS[@]}"; do
        printf '%-8s %s\n' "$check:" "$(tool_version "$check")"
    done

    # without these packages mypy sees Any instead of their types (--ignore-missing-imports): results differ from CI
    if contains mypy "${CHECKS[@]}"; then
        local pkg missing_pkgs=()
        for pkg in "${TYPING_PACKAGES[@]}"; do
            "$PYTHON" -c "import importlib.metadata as m; m.distribution('$pkg')" 2>/dev/null || missing_pkgs+=("$pkg")
        done
        if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
            echo "Warning: not installed for mypy: ${missing_pkgs[*]} (their imports are typed Any, see ./init_dev_venv.sh)"
        fi
    fi

    for check in "${CHECKS[@]}"; do
        case "$check" in
            flake8)
                run_check flake8 "$PYTHON" -m flake8 . \
                    --count \
                    --statistics \
                    --max-complexity=10 \
                    --max-line-length=120 \
                    --extend-exclude="$excluded_csv"
                ;;
            mypy)
                # examples/ and pyModbusTCP-cli/ import packages that are not dev tools (schedule, hatchling)
                run_check mypy "$PYTHON" -m mypy . \
                    --ignore-missing-imports \
                    --exclude "$MYPY_EXCLUDE"
                ;;
            ruff)
                run_check ruff "$PYTHON" -m ruff check . \
                    --no-fix \
                    --output-format=concise \
                    --extend-exclude "$excluded_csv"
                ;;
            pytest)
                run_check pytest "$PYTHON" -m pytest -q
                ;;
        esac
    done

    echo ""
    echo "=== Summary ==="
    local i failed=0 total=0 result detail
    for i in "${!RESULT_NAMES[@]}"; do
        detail="${RESULT_DETAILS[$i]}"
        if [[ "${RESULT_STATUS[$i]}" -eq 0 ]]; then
            result="OK"
        else
            result="FAILED"
            failed=$((failed + 1))
            # no result found in the output (the tool crashed...): give its exit status
            [[ -z "$detail" ]] && detail="exit status ${RESULT_STATUS[$i]}"
        fi
        total=$((total + RESULT_TIMES[i]))
        printf '  %-8s %-7s %4ss   %s\n' "${RESULT_NAMES[$i]}" "$result" "${RESULT_TIMES[$i]}" "$detail"
    done
    if [[ $failed -gt 0 ]]; then
        echo "$failed of ${#RESULT_NAMES[@]} check(s) failed (${total}s)"
        return 1
    fi
    echo "All ${#RESULT_NAMES[@]} checks passed (${total}s)"
}

status=0
if [[ $SUMMARY_ONLY -eq 1 ]]; then
    # full report in a file, then only its summary on the terminal
    audit > "$TMP_DIR/report.txt" 2>&1 || status=$?
    [[ -n "$OUT_FILE" ]] && cp "$TMP_DIR/report.txt" "$OUT_FILE"
    sed -n '/^=== Summary ===$/,$p' "$TMP_DIR/report.txt"
    [[ -n "$OUT_FILE" ]] && echo "Full report: $OUT_FILE"
elif [[ -n "$OUT_FILE" ]]; then
    # pipefail: the exit status is the one of audit, not of tee
    audit 2>&1 | tee "$OUT_FILE" || status=$?
else
    audit || status=$?
fi
exit "$status"
