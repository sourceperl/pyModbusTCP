#!/bin/bash

echo "=== Run Flake8 ==="
flake8 . \
    --count \
    --exit-zero \
    --max-complexity=10 \
    --max-line-length=127 \
    --extend-exclude=build/,docs/,venv/,lab/

echo ""
echo "=== Run Mypy ==="
mypy . \
    --ignore-missing-imports \
    --exclude 'build/' \
    --exclude 'docs/' \
    --exclude 'lab/' \
    --exclude 'venv/' \
    --exclude 'tests/' \
    --exclude 'examples/' \
