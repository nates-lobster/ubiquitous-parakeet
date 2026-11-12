#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON_BIN="${PYTHON:-python3}"
WITH_MCP=false
WITH_HARDWARE=false

for option in "$@"; do
  case "$option" in
    --mcp) WITH_MCP=true ;;
    --hardware) WITH_HARDWARE=true ;;
    *) echo "Usage: $0 [--mcp] [--hardware]" >&2; exit 2 ;;
  esac
done

"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
"$PYTHON_BIN" -m venv .venv
EXTRAS=".[dev"
if [[ "$WITH_MCP" == true ]]; then EXTRAS+=",mcp"; fi
if [[ "$WITH_HARDWARE" == true ]]; then EXTRAS+=",hardware"; fi
EXTRAS+="]"
.venv/bin/python -m pip install -e "$EXTRAS"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/parakeet init --workspace .local/workspace
printf '\nReady. Activate with: source .venv/bin/activate\n'
