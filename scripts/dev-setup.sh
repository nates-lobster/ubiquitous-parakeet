#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON_BIN="${PYTHON:-python3}"
EXTRAS=".[dev]"

case "${1:-}" in
  "") ;;
  --mcp) EXTRAS=".[dev,mcp]" ;;
  *) echo "Usage: $0 [--mcp]" >&2; exit 2 ;;
esac

"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install -e "$EXTRAS"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/parakeet init --workspace .local/workspace
printf '\nReady. Activate with: source .venv/bin/activate\n'
