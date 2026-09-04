#!/bin/sh
# Regenerate the static web-mobile snapshot, even when scene checks fail.
# Dependencies: numpy and Pillow. Override the interpreter with PYTHON.
set -eu
if [ "$#" -eq 1 ] && { [ "$1" = "--help" ] || [ "$1" = "-h" ]; }; then
    echo "usage: tools/refresh_checks.sh [--help]"
    exit 0
fi
if [ "$#" -ne 0 ]; then
    echo "usage: tools/refresh_checks.sh [--help]" >&2
    exit 2
fi
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -z "${PYTHON:-}" ]; then
    if [ -x "$ROOT/.venv-check/bin/python" ]; then
        PYTHON="$ROOT/.venv-check/bin/python"
    elif [ -x "$HOME/Documents/HDC-VSA-Gaussian-Splatting/.venv/bin/python" ]; then
        PYTHON="$HOME/Documents/HDC-VSA-Gaussian-Splatting/.venv/bin/python"
    elif [ -x "$ROOT/.venv-masks/bin/python" ]; then
        PYTHON="$ROOT/.venv-masks/bin/python"
    else
        PYTHON=python3
    fi
fi
snapshot=$(mktemp "$ROOT/.checks.XXXXXX")
trap 'rm -f "$snapshot"' EXIT HUP INT TERM
status=0
"$PYTHON" "$ROOT/tools/check_deliverable.py" --all --json > "$snapshot" || status=$?
if [ "$status" -gt 1 ]; then
    exit "$status"
fi
# Exit 1 means either failed checks or a crash: publish only complete valid JSON.
"$PYTHON" - "$snapshot" "$ROOT/scenes.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    data = json.load(stream)
with open(sys.argv[2]) as stream:
    catalog = json.load(stream)
assert data["schema_version"] == 1
results = data["results"]
assert len(results) == len(catalog)
assert {r["scene"] for r in results} == {s["stem"] for s in catalog}
assert all(r["platform"] == "web-mobile" and isinstance(r["passed"], bool) and r["checks"] for r in results)
PY
mv "$snapshot" "$ROOT/checks.json"
printf '%s\n' "Updated checks.json (checker exit status: $status)"
