#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
QUICK=false

if [ "$#" -gt 1 ]; then
    echo "usage: tools/gate.sh [--quick]" >&2
    exit 2
fi
if [ "$#" -eq 1 ]; then
    if [ "$1" != "--quick" ]; then
        echo "usage: tools/gate.sh [--quick]" >&2
        exit 2
    fi
    QUICK=true
fi

cd "$ROOT"

echo "==> Ruff"
if command -v ruff >/dev/null 2>&1; then
    RUFF=ruff
elif "$PYTHON" -m ruff --version >/dev/null 2>&1; then
    RUFF="$PYTHON -m ruff"
elif command -v uvx >/dev/null 2>&1; then
    RUFF="uvx ruff"
else
    echo "ruff not found: pip install -r requirements-gates.txt" >&2
    exit 1
fi
$RUFF check --select E,F,B,RUF --line-length 120 tools tests

if [ "$QUICK" = true ]; then
    echo "==> Path hygiene"
    "$PYTHON" tools/scan_paths.py

    echo "==> Pytest"
    "$PYTHON" -m pytest -q tests
    exit 0
fi

echo "==> Pytest"
"$PYTHON" -m pytest -q tests

echo "==> Deliverables"
"$PYTHON" tools/check_deliverable.py --all --json

echo "==> Accessibility"
"$PYTHON" tools/a11y_check.py --all

echo "==> Results freshness"
"$PYTHON" tools/results_table.py --check

echo "==> Path hygiene"
"$PYTHON" tools/scan_paths.py
